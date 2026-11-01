import json, os, time, urllib.error, urllib.request
def post(url,payload,headers=None):
    request=urllib.request.Request(url,data=json.dumps(payload).encode(),method="POST"); request.add_header("Content-Type","application/json")
    for key,value in (headers or {}).items(): request.add_header(key,value)
    with urllib.request.urlopen(request,timeout=120) as response: return json.loads(response.read().decode())
def execute(version,task):
    started=time.perf_counter(); spec,provider,model=version["specification"],version["provider"],version["model"]
    try:
        if provider=="ollama": result=post("http://127.0.0.1:11434/api/generate",{"model":model or "qwen3:8b","system":spec["instructions"],"prompt":task,"stream":False}).get("response","Ollama returned an empty response.")
        elif provider=="openai-compatible":
            base=os.environ.get("AGENT_FACTORY_API_BASE","http://127.0.0.1:1234/v1").rstrip("/"); key=os.environ.get("AGENT_FACTORY_API_KEY",""); headers={"Authorization":f"Bearer {key}"} if key else {}
            result=post(f"{base}/chat/completions",{"model":model or "local-model","messages":[{"role":"system","content":spec["instructions"]},{"role":"user","content":task}],"temperature":0.2},headers)["choices"][0]["message"]["content"]
        else: result="DEMO RUN — no model or tool was invoked.\n\n## Findings\nTask received: "+task+"\n\n## Assumptions\nThe task is evaluated using the registered specification and no external evidence has been supplied.\n\n## Recommended next steps\n1. Connect a local Ollama model to run this agent privately.\n2. Add test cases with acceptance phrases before promoting the version.\n3. Keep any requested changes behind a human approval gate."
        return result,"completed",round((time.perf_counter()-started)*1000)
    except (KeyError,ValueError,urllib.error.URLError,urllib.error.HTTPError) as exc: return f"Run failed: {exc}","failed",round((time.perf_counter()-started)*1000)
def evaluate(output,test_case):
    expected=test_case["must_include"].strip()
    if not expected: return True,100,"No required phrase was set; this is a manual-review test."
    passed=expected.lower() in output.lower(); return passed,100 if passed else 0,f"Required phrase {'was' if passed else 'was not'} found: “{expected}”."
