# Repository Assistant

A local LangChain and Streamlit chatbot for inspecting the repository
call-graph database and cloning GitHub repositories with human approval.

## Run it

Prerequisites:

- Python 3.12
- Git
- Ollama running at `http://127.0.0.1:11434`
- The `qwen3.5:2b-q4_K_M` Ollama model

Install the Python packages:

```powershell
python -m pip install -r requirements.txt
```

Start Ollama if it is not already running, then launch the interface:

```powershell
python -m streamlit run app.py
```

To use another installed Ollama model:

```powershell
$env:OLLAMA_MODEL = "your-model-name"
python -m streamlit run app.py
```

Open the local URL printed by Streamlit. Use **New chat** to create independent
chat sessions. Clone requests pause until you approve or reject the proposed
tool call. After a successful clone, the assistant asks whether you want to
parse the repository into the call-graph database. Parsing is a separate tool
call with its own approval prompt.

Cloned repositories are placed in the generated `repositories/` directory.
