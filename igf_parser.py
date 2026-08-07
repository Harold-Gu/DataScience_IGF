import os
import re
import json
import hashlib
import asyncio
import aiohttp
from pathlib import Path
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
from igf_common import decompose_noise_tags, safe_extract_field


class BaseSessionSchema(BaseModel):
    """Shared base fields for all session types"""
    title: str = Field(default="", description="Full session title")
    session_type: str = Field(default="", description="Session type, e.g. Workshop, Open Forum")
    year: int = Field(default=None, description="Year the session was held")

    organizers: str = Field(default="", description="Organizer or host organization name")
    speakers: list[str] = Field(default=[], description="List of main speakers or panelists")
    stakeholder_groups: list[str] = Field(default=[], description="Stakeholder groups involved (e.g. Government, Civil Society)")
    regional_focus: str = Field(default="Global", description="Geographic region of focus")

    participant_count: int = Field(default=None, description="Total on-site participant count")

    themes: list[str] = Field(default=[], description="1-3 core theme tags")
    key_issues: str = Field(default="", description="Brief summary of core discussion points")
    policy_recommendations: str = Field(default="", description="Agreed consensus, policy suggestions, or follow-up actions")


class NetworkingSessionSchema(BaseSessionSchema):
    """Custom schema for Networking Session type"""
    format: str = Field(default="", description="Interactive format (e.g. Interactive Networking, Breakout Discussion)")
    networking_goals: str = Field(default="", description="Networking goals (e.g. partnerships, knowledge sharing, community building)")
    agenda: str = Field(default="", description="Session flow or interactive segment design")
    collaboration_outcomes: str = Field(default="", description="Potential collaboration outcomes or follow-up actions, if any")


class DayZeroSchema(BaseSessionSchema):
    """Custom schema for Day-0 preparatory session type"""
    subtheme: str = Field(default="", description="Session sub-theme")
    format: str = Field(default="", description="Session format (e.g. Workshop, Theatre, Training)")
    description: str = Field(default="", description="Detailed session description")
    onsite_moderator: str = Field(default="", description="On-site moderator")
    online_moderator: str = Field(default="", description="Online moderator")
    rapporteur: str = Field(default="", description="Rapporteur / note-taker")
    sdgs: list[str] = Field(default=[], description="SDG numbers and names")
    preparatory_focus: str = Field(default="", description="Goal or role of the session as a preparatory event")


class LightningTalkSchema(BaseSessionSchema):
    """Custom schema for Lightning Talk type"""
    talk_topics: list[str] = Field(default=[], description="Topics of each short talk")
    format: str = Field(default="", description="Short talk format (e.g. 5-min talks + Q&A)")
    key_takeaways: str = Field(default="", description="Overall key message summary")
    number_of_talks: int = Field(default=None, description="Number of talks included")


class LaunchesAwardsSchema(BaseSessionSchema):
    """Custom schema for Launches & Awards type"""
    initiative_or_product_name: str = Field(default="", description="Name of launched initiative/report/tool/award")
    launch_purpose: str = Field(default="", description="Purpose of the launch or award ceremony")
    target_audience: str = Field(default="", description="Target users or audience")
    stakeholders_involved: str = Field(default="", description="Key organizations or individuals involved")
    impact_expectation: str = Field(default="", description="Expected impact or long-term significance")


# Backward compatibility alias
IGFSessionSchema = BaseSessionSchema



def get_schema_and_prompt(folder_name: str, text_content: str = "") -> tuple[type[BaseModel], str]:
    """
    Auto-detect session type from folder name (priority) + text keywords (fallback).
    Returns (Pydantic schema class, customized prompt template).
    """
    folder_lower = folder_name.lower()
    text_lower = text_content.lower() if text_content else ""

    def _match(*keywords) -> bool:
        return any(kw in folder_lower for kw in keywords) or any(kw in text_lower for kw in keywords)

    if _match("networking"):
        schema = NetworkingSessionSchema
        extra_rules = """
        - Identify interactive and community-building aspects.
        - Extract networking goals such as partnerships, collaboration, or knowledge exchange.
        - Agenda should reflect interactive elements (roundtables, discussions, matchmaking).
        - Capture any mention of follow-up collaboration or outputs.
        """

    elif _match("day-0", "day0", "day 0", "preparatory", "pre-conference", "pre conference"):
        schema = DayZeroSchema
        extra_rules = """
        - These are preparatory sessions before the main IGF.
        - Focus on capacity building, training, or pre-discussion themes.
        - Extract moderators and rapporteurs carefully.
        - Identify SDGs clearly if mentioned.
        - Capture the preparatory purpose (why this session exists before main event).
        """

    elif _match("lightning", "lightning talk", "short talk", "flash talk", "5-minute"):
        schema = LightningTalkSchema
        extra_rules = """
        - Lightning Talks consist of multiple short presentations.
        - Extract each talk topic into "talk_topics".
        - Estimate number_of_talks if not explicitly stated.
        - Format usually includes short time-limited talks + brief discussion.
        - Summarize key_takeaways across all talks, not just one.
        """

    elif _match("launch", "award", "release", "unveil", "prize", "ceremony"):
        schema = LaunchesAwardsSchema
        extra_rules = """
        - Identify clearly what is being launched or awarded.
        - Extract full name of initiative/report/tool/award.
        - Capture purpose and significance (why it matters).
        - Identify stakeholders involved (organizations, institutions).
        - Extract intended audience or beneficiaries.
        - Highlight expected impact.
        """

    else:
        schema = BaseSessionSchema
        extra_rules = "- Extract general session details accurately."

    schema_json = json.dumps(schema.model_json_schema(), indent=2, ensure_ascii=False)

    prompt_template = """
    You are a structured data extraction engine processing Internet Governance Forum meeting records.

    Must strictly output as a valid JSON object matching this JSON Schema:
    __SCHEMA_JSON__

    Extraction Rules:
    1. For string fields, use "" if data cannot be found.
    2. For numeric fields, use null if data cannot be found.
    3. For list fields, use [] if data cannot be found.
    4. Do NOT hallucinate data.
    5. Prefer exact wording from source text.

    __EXTRA_RULES__

    Text content:
    __TEXT_CONTENT__
    """

    prompt_template = (
        prompt_template
        .replace("__SCHEMA_JSON__", schema_json)
        .replace("__EXTRA_RULES__", extra_rules)
    )

    return schema, prompt_template



CACHE_FILE = ".igf_processed_cache.json"


def _get_file_hash(file_path: Path, chunk_size: int = 65536) -> str:
    """Compute SHA256 hash of file content for identity comparison"""
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            sha256.update(chunk)
    return sha256.hexdigest()


def load_processed_cache() -> dict:
    """Load persisted cache of processed files"""
    cache_path = Path(CACHE_FILE)
    if cache_path.exists():
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_processed_cache(cache: dict) -> None:
    """Persist cache of processed files to disk"""
    cache_path = Path(CACHE_FILE)
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except IOError as e:
        print(f"[WARNING] Failed to save cache: {e}")


def is_file_processed(file_path: Path, cache: dict) -> tuple[bool, str]:
    """
    Check if file has already been processed.
    Returns (is_processed, file_hash).
    Strategy: fast path (size+mtime) first, then accurate content hash.
    """
    try:
        stat = file_path.stat()
        size = stat.st_size
        mtime = stat.st_mtime

        fast_key = f"{str(file_path)}::{size}::{mtime}"
        if fast_key in cache:
            return True, cache[fast_key]["hash"]

        file_hash = _get_file_hash(file_path)
        for entry in cache.values():
            if entry.get("hash") == file_hash:
                cache[fast_key] = {"hash": file_hash, "result": entry.get("result")}
                return True, file_hash

        return False, file_hash
    except (OSError, IOError):
        return False, ""


def mark_file_processed(file_path: Path, file_hash: str, result: dict, cache: dict) -> None:
    """Mark file as processed and cache extraction result"""
    try:
        stat = file_path.stat()
        fast_key = f"{str(file_path)}::{stat.st_size}::{stat.st_mtime}"
        cache[fast_key] = {
            "hash": file_hash,
            "result": result
        }
    except (OSError, IOError):
        pass



def clean_html_for_llm(html_content: str) -> str:
    """Strip irrelevant tags and extract plain text to save ~80% tokens"""
    soup = BeautifulSoup(html_content, 'lxml')

    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'svg', 'button', 'form']):
        tag.decompose()

    text = soup.get_text(separator=' ')
    cleaned_text = re.sub(r'\s+', ' ', text).strip()
    return cleaned_text


def parse_llm_json_response(raw_response_text: str) -> dict:
    """Robustly parse JSON from LLM output (handles empty, markdown, stray text)"""
    if not raw_response_text or not raw_response_text.strip():
        raise ValueError("Received empty response from LLM.")

    cleaned = raw_response_text.strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"(\{[\s\S]*\})", cleaned)
        if match:
            return json.loads(match.group(1))
        raise


def _get_schema_defaults(schema_cls: type[BaseModel]) -> dict:
    """Generate default fallback dict matching a schema class"""
    dummy = schema_cls()
    data = dummy.model_dump()
    return data


def normalize_against_schema(data: dict, schema_cls: type[BaseModel]) -> dict:
    """
    Normalize raw LLM output against the schema:
      - Fill in defaults for missing fields
      - Strip unknown fields
      - Coerce types where possible (e.g. "123" -> 123 for int, "" -> None for nullable)
    Never raises; returns a dict guaranteed to match schema shape.
    """
    defaults = _get_schema_defaults(schema_cls)
    schema_fields = schema_cls.model_fields

    out = {}
    for field_name, field_info in schema_fields.items():
        raw = data.get(field_name, defaults.get(field_name))
        expected_type = field_info.annotation

        try:
            if expected_type is str:
                if raw is None:
                    out[field_name] = defaults.get(field_name, "")
                else:
                    out[field_name] = str(raw)
            elif expected_type is int:
                if raw is None or raw == "":
                    out[field_name] = defaults.get(field_name)
                else:
                    out[field_name] = int(raw)
            elif expected_type is list[str] or (
                hasattr(expected_type, "__origin__")
                and expected_type.__origin__ is list
            ):
                if raw is None or raw == "":
                    out[field_name] = defaults.get(field_name, [])
                elif isinstance(raw, list):
                    out[field_name] = [str(x) for x in raw]
                else:
                    out[field_name] = [str(raw)]
            else:
                out[field_name] = raw if raw is not None else defaults.get(field_name)
        except (ValueError, TypeError):
            out[field_name] = defaults.get(field_name)

    return out


def get_fallback_data(file_path: Path, error_msg: str, schema_cls: type[BaseModel] = BaseSessionSchema) -> dict:
    """Return a graceful fallback dict matching the dynamic schema"""
    data = _get_schema_defaults(schema_cls)
    data["_meta_file"] = file_path.name
    data["_meta_folder"] = file_path.parent.name
    data["error"] = error_msg
    return data



async def extract_data_via_llm(
    session: aiohttp.ClientSession,
    file_path: Path,
    semaphore: asyncio.Semaphore,
    cache: dict
) -> dict:
    """
    Drive local qwen3:8b model:
    1. Check dedup cache; hit -> return cached result
    2. Auto-detect session type -> select schema + custom prompt
    3. Call local Ollama API for extraction
    """
    processed, file_hash = is_file_processed(file_path, cache)
    if processed and file_hash:
        for entry in cache.values():
            if entry.get("hash") == file_hash and "result" in entry:
                print(f"[CACHE HIT] Skipping (already processed): {file_path.name}")
                cached_result = entry["result"]
                cached_result["_meta_file"] = file_path.name
                cached_result["_meta_folder"] = file_path.parent.name
                return cached_result
        print(f"[CACHE HIT] Same content detected (re-extracting for metadata): {file_path.name}")

    async with semaphore:
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    html_content = f.read()

                compressed_text = clean_html_for_llm(html_content)

                char_limit = 3500 if attempt == 0 else 1800
                safe_text = compressed_text[:char_limit]

                schema_cls, prompt_template = get_schema_and_prompt(file_path.parent.name, compressed_text)

                prompt = prompt_template.replace("__TEXT_CONTENT__", safe_text)

                url = "http://localhost:11434/api/generate"
                payload = {
                    "model": "qwen3:8b",
                    "prompt": prompt,
                    "format": "json",
                    "stream": False,
                    "options": {
                        "temperature": 0.1
                    }
                }

                async with session.post(url, json=payload, timeout=240) as response:
                    if response.status == 200:
                        result = await response.json()
                        raw_resp = result.get('response', '')

                        extracted_json = parse_llm_json_response(raw_resp)
                        extracted_json = normalize_against_schema(extracted_json, schema_cls)

                        extracted_json['_meta_file'] = file_path.name
                        extracted_json['_meta_folder'] = file_path.parent.name

                        if not file_hash:
                            file_hash = _get_file_hash(file_path)
                        mark_file_processed(file_path, file_hash, extracted_json, cache)

                        print(f"[SUCCESS] AI parsing completed [{schema_cls.__name__}]: {file_path.name}")
                        return extracted_json
                    else:
                        print(f"[ERROR] API status code {response.status}: {file_path.name} (Attempt {attempt + 1})")
                        if attempt == max_retries:
                            fallback = get_fallback_data(file_path, f"API Status {response.status}", schema_cls)
                            if not file_hash:
                                file_hash = _get_file_hash(file_path)
                            mark_file_processed(file_path, file_hash, fallback, cache)
                            return fallback

            except (ValueError, json.JSONDecodeError, asyncio.TimeoutError, aiohttp.ClientError) as e:
                print(f"[WARNING] Attempt {attempt + 1} failed for {file_path.name}: {type(e).__name__} - {str(e)}")
                if attempt == max_retries:
                    schema_cls, _ = get_schema_and_prompt(file_path.parent.name)
                    fallback = get_fallback_data(file_path, f"Failed after retries: {type(e).__name__}", schema_cls)
                    if not file_hash:
                        try:
                            file_hash = _get_file_hash(file_path)
                        except Exception:
                            file_hash = ""
                    if file_hash:
                        mark_file_processed(file_path, file_hash, fallback, cache)
                    return fallback
                await asyncio.sleep(1)
            except Exception as e:
                print(f"[EXCEPTION] Critical failure for file {file_path.name}: {type(e).__name__} - {str(e)}")
                schema_cls, _ = get_schema_and_prompt(file_path.parent.name)
                fallback = get_fallback_data(file_path, f"Critical: {type(e).__name__}", schema_cls)
                try:
                    if not file_hash:
                        file_hash = _get_file_hash(file_path)
                    if file_hash:
                        mark_file_processed(file_path, file_hash, fallback, cache)
                except Exception:
                    pass
                return fallback



async def main_pipeline(base_path: str = ".", max_concurrency: int = 1):
    """Pipeline entrypoint"""
    cache = load_processed_cache()
    print(f"[CACHE] Loaded {len(cache)} cached entries.")

    base_dir = Path(base_path)
    target_files = [
        p for p in base_dir.rglob("*.[hH][tT][mM]*")
        if ".venv" not in p.parts and ".idea" not in p.parts and ("data_" in p.parent.name or "igf_" in p.parent.name)
    ]

    pending_files = []
    cache_hit_count = 0
    for fp in target_files:
        processed, _ = is_file_processed(fp, cache)
        if processed:
            cache_hit_count += 1
            print(f"[SKIP] Already processed: {fp.name}")
        else:
            pending_files.append(fp)

    total_scanned = len(target_files)
    total_new = len(pending_files)

    print(
        f"Found {total_scanned} web files. {total_new} new files to process. "
        f"Launching AI async extraction pool (Concurrency: {max_concurrency})..."
    )

    semaphore = asyncio.Semaphore(max_concurrency)

    async with aiohttp.ClientSession() as session:
        tasks = [extract_data_via_llm(session, file_path, semaphore, cache) for file_path in pending_files]
        new_results = await asyncio.gather(*tasks) if tasks else []

    save_processed_cache(cache)
    print(f"[CACHE] Saved cache with {len(cache)} entries.")

    all_results = []
    seen_hashes = set()
    for entry in cache.values():
        h = entry.get("hash")
        if h and h not in seen_hashes and "result" in entry:
            seen_hashes.add(h)
            all_results.append(entry["result"])
    all_results.extend(new_results)

    # ============== Statistics & Success Rate ==============
    def _is_success(res: dict) -> bool:
        return "error" not in res

    new_success = sum(1 for r in new_results if _is_success(r))
    new_failed = total_new - new_success

    overall_success = sum(1 for r in all_results if _is_success(r))
    overall_total = len(all_results)
    overall_failed = overall_total - overall_success

    new_rate = (new_success / total_new * 100) if total_new > 0 else 0.0
    overall_rate = (overall_success / overall_total * 100) if overall_total > 0 else 0.0

    bar_len = 60
    sep = "=" * bar_len


    outcome_per_file = {}

    for entry in cache.values():
        res = entry.get("result")
        if not res:
            continue
        fname = res.get("_meta_file", "")
        ffolder = res.get("_meta_folder", "")
        key = (ffolder, fname)
        if key and key not in outcome_per_file:
            outcome_per_file[key] = res

    for res in new_results:
        fname = res.get("_meta_file", "")
        ffolder = res.get("_meta_folder", "")
        key = (ffolder, fname)
        if key:
            outcome_per_file[key] = res

    scan_success = 0
    scan_failed = 0
    scan_missing = 0
    for fp in target_files:
        key = (fp.parent.name, fp.name)
        res = outcome_per_file.get(key)
        if res is None:
            scan_missing += 1
        elif _is_success(res):
            scan_success += 1
        else:
            scan_failed += 1
    scan_rate = (scan_success / total_scanned * 100) if total_scanned > 0 else 0.0

    print()
    print(sep)
    print(" EXTRACTION SUMMARY")
    print(sep)
    print(f"  Scanned HTML files           : {total_scanned}")
    print(f"     Cache hits (skipped)    : {cache_hit_count}")
    print(f"    Processed this run      : {total_new}")
    print()
    print(f"  This run results")
    print(f"  Succeeded       : {new_success:<{bar_len}}")
    print(f"  Failed          : {new_failed:<{bar_len}}")
    print(f"  Success rate    : {new_rate:5.1f}%{'':<{bar_len}}")

    print()
    print(f"  Against all scanned files")
    print(f"  Succeeded       : {scan_success:<{bar_len}}")
    print(f"  Failed          : {scan_failed:<{bar_len}}")
    print(f"  No result       : {scan_missing:<{bar_len}}")
    print(f"  Success rate    : {scan_rate:5.1f}%{'':<{bar_len}}")

    print()
    print(f"   Unique content in output JSON ")
    print(f"   Total records   : {overall_total:<{bar_len}}")
    print(f"   Succeeded       : {overall_success:<{bar_len}}")
    print(f"   Failed          : {overall_failed:<{bar_len}}")
    print(f"   Success rate    : {overall_rate:5.1f}%{'':<{bar_len }}")

    print(sep)

    output_file = "igf_ai_extracted_data.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    print(f"\nAll parsing complete! Structured JSON saved to: {output_file}")


if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main_pipeline(base_path=".", max_concurrency=4))
