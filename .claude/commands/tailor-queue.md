---
description: Tailor resumes and answer drafts for every approved job, then summarize what needs review
---

Run the tailoring batch for approved jobs:

```
uv run iap tailor $ARGUMENTS
```

(`$ARGUMENTS` may be `--job <id>` for one job or `-n <count>` to cap the batch.)

Then report, per job: company and title, ATS score, the gaps listed, any verifier notes about
reverted wording, and which questions or cover letters still need approval. Finish with:
"Review and approve in the dashboard: `uv run iap dashboard`."

Do not edit the profile, the database, or any generated resume yourself. Tailoring and its
never-invent checks run inside `iap tailor`; your job here is only to run it and summarize.
