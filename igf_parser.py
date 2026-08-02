import os
import re
import json
import asyncio
import aiohttp
from pathlib import Path
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field


# ==========================================
# [Lightweight Rule Routing] Pydantic Schema Definition
# ==========================================
class IGFSessionSchema(BaseModel):
    # 基础元数据
    title: str = Field(default="", description="会议完整标题")
    session_type: str = Field(default="", description="会议类型，例如 Workshop, Open Forum 等")
    year: int = Field(default=None, description="会议召开的年份")

    # 实体与属性
    organizers: str = Field(default="", description="组织者或负责机构名称")
    speakers: list[str] = Field(default=[], description="主要发言人或小组成员名单")
    stakeholder_groups: list[str] = Field(default=[],
                                          description="涉及的利益相关方（如 Government, Civil Society, Private Sector 等）")
    regional_focus: str = Field(default="Global", description="会议讨论聚焦的地理区域")

    # 统计数据
    participant_count: int = Field(default=None, description="现场整体参会人数")
    women_count: int = Field(default=None, description="女性参会人数")

    # 核心内容提炼 (为下游 IR 系统准备)
    themes: list[str] = Field(default=[], description="会议所属的 1-3 个核心主题标签")
    key_issues: str = Field(default="", description="核心议题的简要总结")
    policy_recommendations: str = Field(default="", description="达成的共识、政策建议或具体的后续行动倡议")


# ==========================================
# [DOM Coarse-Grained Denoising Module]
# ==========================================
def clean_html_for_llm(html_content: str) -> str:
    """Quickly strip out script/style/nav/footer and extract plain text to save 80% tokens"""
    soup = BeautifulSoup(html_content, 'lxml')

    # Ruthlessly strip irrelevant layout tags
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'svg', 'button', 'form']):
        tag.decompose()

    # Extract plain text from the remaining body
    text = soup.get_text(separator=' ')

    # Compress redundant whitespace and newlines for maximum token efficiency
    cleaned_text = re.sub(r'\s+', ' ', text).strip()
    return cleaned_text


def parse_llm_json_response(raw_response_text: str) -> dict:
    """Robustly parse JSON from LLM output, handling empty responses, markdown wrappers and stray text"""
    if not raw_response_text or not raw_response_text.strip():
        raise ValueError("Received empty response from LLM.")

    cleaned = raw_response_text.strip()

    # Remove markdown code blocks if present (e.g. ```json ... ```)
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    # Try direct parsing
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Fallback: try to extract the first JSON object block using regex
        match = re.search(r"(\{[\s\S]*\})", cleaned)
        if match:
            return json.loads(match.group(1))
        raise


def get_fallback_data(file_path: Path, error_msg: str) -> dict:
    """Provide a graceful fallback dictionary matching the full Schema to ensure field alignment"""
    return {
        # 基础元数据
        "title": "",
        "session_type": "",
        "year": None,

        # 实体与属性
        "organizers": "",
        "speakers": [],
        "stakeholder_groups": [],
        "regional_focus": "Global",

        # 统计数据
        "participant_count": None,
        "women_count": None,

        # 核心内容提炼
        "themes": [],
        "key_issues": "",
        "policy_recommendations": "",

        # 溯源与错误标记
        "_meta_file": file_path.name,
        "_meta_folder": file_path.parent.name,
        "error": error_msg
    }


# ==========================================
# [Async Concurrent Extraction Engine] (Asyncio + Ollama)
# ==========================================
async def extract_data_via_llm(session: aiohttp.ClientSession, file_path: Path, semaphore: asyncio.Semaphore) -> dict:
    """Drive the local model with strict output constraints, updated prompt, and robust fallback alignment"""
    async with semaphore:
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    html_content = f.read()

                # Perform denoising
                compressed_text = clean_html_for_llm(html_content)

                # Progressive text truncation based on attempts
                char_limit = 3500 if attempt == 0 else 1800
                safe_text = compressed_text[:char_limit]

                # 优化后的结构化提示词：明确约束字段类型（如数组、数字、字符串）
                prompt = f"""
                You are a structured data extraction engine. Please extract the required information from the following meeting record text.
                Must strictly output as a valid JSON object matching this JSON Schema:
                {IGFSessionSchema.model_json_schema()}

                Extraction Rules:
                1. For string fields (like title, organizers, key_issues, policy_recommendations), use "" if data cannot be found.
                2. For numeric fields (like year, participant_count, women_count), use null if data cannot be found.
                3. For list fields (like speakers, stakeholder_groups, themes), use a valid JSON array [] if data cannot be found.
                4. Do not output any markdown formatting (like ```json) or extra text outside the JSON object.

                Text content:
                {safe_text}
                """

                # Request local Ollama API (default port 11434)
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

                # Extended timeout to 120 seconds
                async with session.post(url, json=payload, timeout=240) as response:
                    if response.status == 200:
                        result = await response.json()
                        raw_resp = result.get('response', '')

                        # Use the robust cleaner to decode JSON with empty check
                        extracted_json = parse_llm_json_response(raw_resp)

                        # Append file metadata
                        extracted_json['_meta_file'] = file_path.name
                        extracted_json['_meta_folder'] = file_path.parent.name

                        print(f"[SUCCESS] AI parsing completed: {file_path.name}")
                        return extracted_json
                    else:
                        print(f"[ERROR] API status code {response.status}: {file_path.name} (Attempt {attempt + 1})")
                        if attempt == max_retries:
                            return get_fallback_data(file_path, f"API Status {response.status}")

            except (ValueError, json.JSONDecodeError, asyncio.TimeoutError, aiohttp.ClientError) as e:
                print(f"[WARNING] Attempt {attempt + 1} failed for {file_path.name}: {type(e).__name__} - {str(e)}")
                if attempt == max_retries:
                    return get_fallback_data(file_path, f"Failed after retries: {type(e).__name__}")
                await asyncio.sleep(1)  # Short wait before retry
            except Exception as e:
                print(f"[EXCEPTION] Critical failure for file {file_path.name}: {type(e).__name__} - {str(e)}")
                return get_fallback_data(file_path, f"Critical: {type(e).__name__}")


# ==========================================
# Main Control Flow & [Structured Data Ingestion]
# ==========================================
async def main_pipeline(base_path: str = ".", max_concurrency: int = 1):
    """Pipeline main function"""
    base_dir = Path(base_path)
    # Get all html files, excluding virtual environments
    target_files = [
        p for p in base_dir.rglob("*.[hH][tT][mM]*")
        if ".venv" not in p.parts and ".idea" not in p.parts and ("data_" in p.parent.name or "igf_" in p.parent.name)
    ]

    print(
        f"Found {len(target_files)} web files. Preparing to launch AI async concurrent extraction pool (Concurrency: {max_concurrency})...")

    semaphore = asyncio.Semaphore(max_concurrency)

    async with aiohttp.ClientSession() as session:
        tasks = [extract_data_via_llm(session, file_path, semaphore) for file_path in target_files]
        results = await asyncio.gather(*tasks)

    output_file = "igf_ai_extracted_data.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\nAll parsing complete! Structured JSON data saved to: {output_file}")


if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main_pipeline(base_path=".", max_concurrency=4))