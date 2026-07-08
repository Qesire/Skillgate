# Normalized Skill Input

## Selected Skill

- **Skill:** `experiment_debug` — Experiment Debug
- **Decision:** `explore_first`
- **Reason:** Important context can be discovered through read-only local inspection.

## User Request

the experiment is broken

## Agent-Discoverable Inputs

> These are codebase facts. The agent should discover them through read-only exploration.

- Which component failed: data loading, model inference, scoring, or output serialization

## Safe Defaults

- Do not change the experiment protocol or evaluation metric
- Do not alter the experiment's random seed or data split

## Activation Instruction

Activate the `experiment_debug` skill with the inputs above.

Before editing, perform read-only discovery for agent-discoverable slots.
Stop and ask the user only if a required non-discoverable input is missing.
Complete local exploration before proposing changes.
