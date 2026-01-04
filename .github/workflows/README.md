# GitHub Actions Workflows

This directory contains CI/CD workflows for each service in the Tic-Tac-Toe monorepo.

## Workflows

Each service has its own workflow that:
1. **Runs tests** (or syntax checks) before building
2. **Builds Docker image** when code is pushed
3. **Updates Helm chart** appVersion in the separate helm charts repository

### Services with Workflows

- **game-logic-service**: Runs unit tests (`test_game.py`)
- **gateway-service**: Runs Python syntax check
- **archive-service**: Runs Python syntax check
- **statistics-service**: Runs Python syntax check
- **frontend**: Runs TypeScript type checking

## Required GitHub Secrets

Configure these secrets in your GitHub repository settings:

### Docker Registry
- `DOCKER_REGISTRY`: Docker registry URL (e.g., `ghcr.io` or `docker.io`)
- `DOCKER_USERNAME`: Docker registry username
- `DOCKER_PASSWORD`: Docker registry password/token

### Helm Charts Repository
- `HELM_CHART_REPO`: GitHub repository path for helm charts (e.g., `username/tic-tac-toe-helm`)
- `HELM_CHART_TOKEN`: GitHub Personal Access Token with repo permissions to push to helm charts repo

## How It Works

1. **Trigger**: Workflow triggers on push to `main` or `develop` branches when files in the service directory change
2. **Test**: Runs tests/syntax checks
3. **Build**: Creates Docker image with version tag (timestamp + commit SHA)
4. **Push**: Pushes image to Docker registry with both version tag and `latest` tag
5. **Update Helm**: Checks out helm charts repo, updates `appVersion` in Chart.yaml, commits and pushes

## Version Format

Version tags follow the format: `YYYYMMDD-HHMMSS-<commit-sha>`

Example: `20241231-143022-a1b2c3d4`

## Workflow Files

- `.github/workflows/game-logic-service.yml`
- `.github/workflows/gateway-service.yml`
- `.github/workflows/archive-service.yml`
- `.github/workflows/statistics-service.yml`
- `.github/workflows/frontend.yml`

