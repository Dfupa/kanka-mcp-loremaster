# Engineering policy

Follow Ponytail's full-mode principle: the smallest complete change, without
sacrificing validation, security, error handling, or tests.
Source: https://github.com/DietrichGebert/ponytail (MIT).

- Use readable, idiomatic Python, four-space indentation, and complete type hints.
- Document public functions with their contracts, relevant parameters, and errors.
- Reuse existing code before adding abstractions or dependencies.
- Validate external inputs, including trivial paths; test branches and failures.
- Keep tools, code, and skills in English. Localize user-facing messages only.
- Never log access tokens, silently overwrite canon, or bypass campaign scopes.
- Keep README.md unchanged until the integration is verified end to end.
- Run tests and lint checks; disclose checks that could not be run.
