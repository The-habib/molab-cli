# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-10-07

### Added
- **Interactive TUI Control Center**: Zero-typing dashboard accessible via `molab` or `molab ui`.
- **First-time Onboarding Wizard**: Automated 30-second guided setup with Clerk auto-discovery.
- **NVIDIA Blackwell GPU Support**: 1-click provisioning and switching to **NVIDIA RTX PRO 6000 Blackwell Server Edition (96 GB VRAM)**.
- **AI Model Studio**:
  - Interactive terminal chat with uncompressed 27B LLMs (`gemma-3-27b-it-abliterated`).
  - Native localhost OpenAI bridge (`molab forward <id> --port 8000`) for Open WebUI, SillyTavern, and Python SDKs.
- **Interactive Root Terminal**: Bidirectional WebSocket PTY connection directly into CoreWeave cloud containers.
- **Real-time GPU Telemetry**: Live VRAM metrics, SM counts, CUDA 13.0, and PyTorch telemetry.
- **Cloud File Transfer**: Bidirectional `push` and `pull` commands.
- **Remote Package Management**: Remote `install` via `uv pip`.
- **Dual CLI Aliases**: Global `molab` and `molabctl` commands.
