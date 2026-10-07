# Contributing to molab-cli

Thank you for your interest in contributing to **molab-cli**!

## 🛠️ Development Setup

1. Fork and clone the repository:
   ```bash
   git clone https://github.com/The-habib/molab-cli.git
   cd molab-cli
   ```

2. Create a virtual environment and install dependencies in editable mode:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e ".[dev]"
   ```

3. Run the test suite:
   ```bash
   pytest tests/ -v
   ```

## 📐 Guidelines

- **Zero-Storage Philosophy**: Ensure that no model weights, heavy checkpoints, or large files are downloaded onto the client device. All heavy compute must remain inside the remote cloud container.
- **Code Style**: Follow PEP 8 and modern Python conventions. Use type hints where appropriate.
- **Error Handling**: Use the `MoLabError` hierarchy in `molab_cli.exceptions` and never let unhandled tracebacks reach the user without friendly remediation advice.
- **Pull Requests**:
  - Keep commits focused and provide clear descriptions.
  - Ensure all tests pass prior to submitting a PR.
