# Deployment

Scout runs on a Google Compute Engine VM.

| | |
|---|---|
| Project | `ja-scout-yt` |
| VM | `scout-bot-vm` |
| Zone | `us-east1-c` |
| Machine type | `e2-micro` |
| Image | `us-east1-docker.pkg.dev/ja-scout-yt/scout-repo/scout-bot:latest` |

The VM is Container-Optimized OS. systemd unit `scout.service` runs the container. `Restart=always` brings it back after a crash and on boot.

Environment variables for the process live in `/etc/scout.env` on the VM (mode 600). They are not in git. Names are listed in [configuration](configuration.md).

A background HTTP server listens on `PORT` (default 8080) and returns `200` with the body `ok`. It does not serve the bot. Slack traffic uses Socket Mode.

## Redeploy

From a checkout of the repo, build and push the image:

```bash
sudo docker build -t us-east1-docker.pkg.dev/ja-scout-yt/scout-repo/scout-bot:latest .

# Re-authenticate Docker if the token has expired
ACCESS_TOKEN=$(gcloud auth print-access-token)
echo "$ACCESS_TOKEN" | sudo docker login -u oauth2accesstoken \
  --password-stdin https://us-east1-docker.pkg.dev

sudo docker push us-east1-docker.pkg.dev/ja-scout-yt/scout-repo/scout-bot:latest
```

One-time registry login on the VM (the GCE metadata server provides credentials):

```bash
gcloud compute ssh scout-bot-vm --zone=us-east1-c --project=ja-scout-yt \
  --command="docker-credential-gcr configure-docker --registries=us-east1-docker.pkg.dev"
```

Pull the image and restart:

```bash
gcloud compute ssh scout-bot-vm --zone=us-east1-c --project=ja-scout-yt \
  --command="docker pull us-east1-docker.pkg.dev/ja-scout-yt/scout-repo/scout-bot:latest && sudo systemctl restart scout.service"
```

## Operate the service

```bash
sudo systemctl status scout.service
sudo docker logs -f scout-bot
sudo systemctl restart scout.service
```

The image `CMD` is `python scout_bot.py`. Pushing to `main` does not deploy by itself.
