# NetClone v3: Assistant

Adds `ai/assistant.py`, a small rule-based assistant on top of the v2 models.
It works out what the user is asking for (recommendations, something similar
to a title, info about a title, a mood, or a plain search) and answers with
real catalog titles. There is no LLM involved.

## Run

```powershell
pip install -r requirements.txt
python run.py
```

Same demo as v2, plus a few assistant queries and their replies.
