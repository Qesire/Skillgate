# Skill: Experiment Debug

Debug a failing or unexpected experiment run while preserving the experiment protocol.

## Activation
Triggered when:
- An experiment run produces unexpected results, crashes, or non-deterministic output
- A user asks to debug a research pipeline, benchmark run, or evaluation script

## Execution
1. Read the experiment log or error output.
2. Identify the failing component: data loading, model inference, scoring, or output serialization.
3. Reproduce the failure with the smallest relevant input.
4. Isolate the root cause.
5. Propose a minimal fix.

## Output
- Root cause analysis with log evidence
- A minimal fix that preserves the original experiment intent
- The smallest reproduction command and its result after fix

## Constraints
- Do not change the experiment protocol or evaluation metric.
- Do not modify benchmark scores or expected outputs without explicit authorization.
- Do not alter the experiment's random seed or data split.
- Do not delete experiment results or logs.
- Do not introduce new dependencies.