import sys

sys.path.insert(0, r'C:\Users\guhao\PyCharmMiscProject\llm_extract_benchmark')
script = sys.argv[1]
sys.argv = [script] + sys.argv[2:]
exec(open(script, encoding='utf-8-sig').read())
