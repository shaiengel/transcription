# Configuration Management

This directory contains configuration files for the transcription pipeline.

## File Structure

- `config.<env>.json` - Public configuration (committed to git)
- `config.secrets.<env>.json` - Secrets (NOT committed, required for local dev)
- `config.secrets.example.json` - Template showing required secrets structure
- `render_env.py` - Script to generate .env files from templates

## Setup

1. Copy the secrets template:
   ```bash
   cp config.secrets.example.json config.secrets.dev.json
   ```

2. Fill in your secrets in `config.secrets.dev.json`:
   - `db.password`: Database password for vps_daf-yomi
   - `gitlab.private_token`: GitLab personal access token with `api` and `write_repository` scopes

3. Generate .env files:
   ```bash
   uv run render_env.py dev
   ```

## Usage

```bash
# Render .env files for dev environment (default)
uv run render_env.py

# Render for production
uv run render_env.py prod

# Require secrets file to exist
uv run render_env.py dev --require-secrets

# Skip secret validation (use with caution)
uv run render_env.py dev --skip-validation
```

## Adding New Secrets

1. Add the secret to `config.secrets.example.json` with an empty value
2. Add validation in `render_env.py` if the secret is required
3. Update this README with instructions on obtaining the secret
4. Update your local `config.secrets.<env>.json`

## Adding New Public Config

Just add to `config.<env>.json` directly and commit.

## Security Notes

- **NEVER commit** `config.secrets.*.json` files
- Keep secrets synced with team via secure channels (1Password, etc.)
- Use least-privilege access for service accounts
- Consider using AWS Secrets Manager for production environment

## Troubleshooting

**Error: Config file not found**
- Make sure you're running from the `.config` directory
- Check that `config.dev.json` exists

**Error: Required secrets are missing or empty**
- Create `config.secrets.dev.json` from the example file
- Fill in all required secret values

**Warning: Secrets file not found**
- This is OK if your templates don't use secrets
- Use `--require-secrets` flag to enforce secrets file presence
