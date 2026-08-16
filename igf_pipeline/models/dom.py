"""DOM helpers: noise removal, pagination discovery and Drupal field extraction."""
import re

from ..config import _NOISE_RE
from . import network


def _strip_noise(soup):
    for tag in soup(["script","style","noscript","template","iframe","form","button","input","select","textarea","link"]):
        tag.decompose()
    for tag in soup(["nav","header","footer","aside"]):
        tag.decompose()
    for el in soup.find_all(True):
        if getattr(el,"attrs",None)is None:continue
        cls=" ".join(el.get("class")or[])
        ident=str(el.get("id")or"")
        if _NOISE_RE.search(cls)or _NOISE_RE.search(ident):el.decompose()
    return soup

def _next_page_links(soup,base_url):
    out=[];seen=set()
    for a in soup.find_all("a",href=True):
        href=a["href"].strip()
        if not href or href.startswith("#")or href.startswith("javascript:"):continue
        rel=" ".join(a.get("rel")or[])if isinstance(a.get("rel"),list)else str(a.get("rel")or"")
        title=str(a.get("title")or"")
        cls=" ".join(a.get("class")or[])if isinstance(a.get("class"),list)else str(a.get("class")or"")
        is_next=("next"in rel.lower())or re.search(r"next|next page",title,re.I)
        if not is_next and"next"in cls.lower():is_next=("pager"in cls.lower()or"pagination"in cls.lower())
        if not is_next:continue
        full=network._make_url(href,base_url)
        if full in seen:continue
        seen.add(full);out.append(full)
    return out

def _extract_drupal_fields_json(soup):
    fields={}
    for elem in soup.select("[class*='field--name-field-']"):
        field_name=None
        for cls in elem.get('class',[]):
            m=re.match(r'field--name-field-(.+)',cls)
            if m:field_name=m.group(1).replace('-','_').strip('_').lower();break
        if not field_name:continue
        label_elem=elem.select_one('.field__label')
        label=label_elem.get_text(strip=True)if label_elem else''
        label=re.sub(r'\s*\(.*?\)','',label).strip()
        items=elem.select('.field__item')
        if not items:continue
        contents=[]
        for item in items:
            links=[{'text':a.get_text(strip=True),'href':a['href']}for a in item.find_all('a',href=True)]
            text=item.get_text(separator='\n',strip=True)
            text=re.sub(r'\n{3,}','\n\n',text)
            contents.append({'text':text,'links':links})
        if field_name in fields:fields[field_name]['content'].extend(contents)
        else:fields[field_name]={'label':label,'content':contents}
    return fields
