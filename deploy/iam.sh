#!/usr/bin/env bash
# Grant least-privilege IAM roles to the agent's Agent Identity principal.
#
# IMPORTANT: the principal is derived from the hosting resource's ID, so every
# redeploy creates a NEW principal and old bindings do not carry over.
# Run this script after each deploy (or move it into Terraform / your pipeline).
#
# Deleting an agent also leaves its bindings behind: run `./deploy/iam.sh remove` to clean up.
#
# Fill in the placeholders. Confirm the exact principal path in the docs for your runtime:
# https://docs.cloud.google.com/iam/docs/agent-identity-overview
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
PROJECT_NUMBER="${PROJECT_NUMBER:?set PROJECT_NUMBER}"
ORG_ID="${ORG_ID:?set ORG_ID (use the proj- trust domain instead if you have no organization)}"
LOCATION="${LOCATION:-us-central1}"
AGENT_RESOURCE_ID="${AGENT_RESOURCE_ID:?set to the resource ID of the deployed agent}"

PRINCIPAL="principal://agents.global.org-${ORG_ID}.system.id.goog/resources/aiplatform/projects/${PROJECT_NUMBER}/locations/${LOCATION}/reasoningEngines/${AGENT_RESOURCE_ID}"

# Only what this agent needs. Add one line per resource it really touches.
ROLES=(
  "roles/aiplatform.user"          # call Gemini models
  # "roles/bigquery.dataViewer"    # example: read-only BigQuery access if a tool needs it
)

action="${1:-add}"
for role in "${ROLES[@]}"; do
  if [[ "$action" == "remove" ]]; then
    gcloud projects remove-iam-policy-binding "$PROJECT_ID" --member="$PRINCIPAL" --role="$role"
  else
    gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="$PRINCIPAL" --role="$role" --condition=None
  fi
done
echo "$action done for $PRINCIPAL"
