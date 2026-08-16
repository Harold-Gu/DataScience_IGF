import os
import sys

BASE = r'C:\Users\guhao\PyCharmMiscProject\llm_extract_benchmark'
sys.path.insert(0, BASE)

phase = 'verify'
args = {
    'smoke': ['benchmark.py', '--models', 'qwen3.5:9b', '--methods', 'rules',
              '--docs', 'doc_access_2007,doc_closing_2006,doc_consult_2007'],
    'models': ['benchmark.py', '--methods', 'oneshot',
               '--docs', 'doc_access_2007,doc_closing_2006'],
    'methods': ['benchmark.py', '--models', 'qwen3.5:9b,qwen2.5:latest',
                '--methods', 'fewshot,fieldqa,tools,cited,chunked',
                '--docs', 'doc_access_2007'],
    'verify': ['verify.py', '--self-consistency', '--negatives', '--method', 'oneshot',
               '--models', 'qwen3.5:9b,qwen2.5:latest'],
}[phase]

sys.argv = args
script = os.path.join(BASE, args[0])
exec(open(script, encoding='utf-8-sig').read())
