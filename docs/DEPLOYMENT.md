# Deployment plan (not done yet)

The project runs locally with `docker compose`. A public live demo is optional; the resume link can point at the GitHub repository with screenshots and a short demo video. Nothing below has been deployed or measured yet.

## Free or credit-covered options (checked October 2026; verify before signing up)

| Option | What you get | Fits FrameSeek? |
|---|---|---|
| GitHub Student Developer Pack: **DigitalOcean** | $200 credit for one year | Yes. One 4 GB droplet runs the whole compose stack; index the demo library once, then serve search. |
| GitHub Student Developer Pack: **Azure for Students** | $100 credit, no card required | Yes, a small Linux VM with Docker works the same way. |
| **AWS Free Tier** (new accounts) | up to $200 credits for 6 months on the free plan | Matches the blueprint's AWS target (EC2 + S3). Requires care to stay within credits. |
| Campus compute (e.g. Illinois research computing) | GPU time, if you can get access | Useful for re-indexing with larger Whisper/CLIP models, not for hosting. |

## Recommended path

1. **Single VM demo** (DigitalOcean or Azure credits): Ubuntu VM with 4 GB RAM, install Docker, clone the repo, create `.env` with `FRAMESEEK_AUTH_MODE=token`, strong `FRAMESEEK_INTERNAL_TOKEN` / `FRAMESEEK_MEDIA_SECRET`, run `docker compose up -d`, put Caddy in front for HTTPS. Disable public uploads (token mode) so strangers cannot use your CPU.
2. **AWS version** (only after the local baseline and evaluation exist): artifacts in a private S3 bucket (implement `S3Storage` with the same four methods as `LocalStorage`), worker as an AWS Batch job definition using the same image, API + ML service on one modest EC2 instance, Postgres on the same instance (single-instance database is a demo trade-off). Use Batch's queue for execution and keep Postgres as the source of truth for logical job state; reconcile ambiguous submissions because a database commit and a Batch submit are not atomic.

## Cost hygiene

Agree on a spending ceiling first, set a billing alert (an alert is not a hard cap), stop or destroy idle instances, and record instance, storage and transfer costs separately before quoting any processing price.
