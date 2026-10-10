# MoLab CLI — Terminal UI & UX Design System

## 1. Design Philosophy & Interaction Standards

MoLab CLI is designed as a **modern, keyboard-driven terminal application** providing zero-typing discovery, strict visual hierarchy, predictable navigation, and resilient execution.

It avoids unformatted plain-text dumps and numbered command lists, offering instead:
* Full interactive selection menus with arrow-key navigation.
* Intelligent target auto-resolution (no manual copy-pasting of 24-character pod IDs).
* Contextual status badges and telemetry cards.
* Clean pipe safety: pure machine-readable output in non-interactive pipes and `--json` modes.
* Secret redaction: zero leakage of tokens, cookies, or private keys.

---

## 2. Design Tokens & Color Palette (OLED Slate)

The color palette is optimized for dark terminal emulators, OLED mobile displays (Termux), and modern desktop terminals:

| Token | Hex / Color | Semantic Purpose |
| :--- | :--- | :--- |
| `COLOR_BG` | `#0F172A` | Deep Obsidian Slate background |
| `COLOR_PRIMARY` | `#06B6D4` | Electric Cyan — primary navigation, focus, and borders |
| `COLOR_ACCENT` | `#22C55E` | Emerald Green — successful operations and active states |
| `COLOR_BLACKWELL` | `#76B900` | NVIDIA Lime Green — Blackwell 96GB GPU telemetry and flags |
| `COLOR_WARNING` | `#F59E0B` | Amber — occupancy warnings, approaching quotas, caveats |
| `COLOR_DANGER` | `#EF4444` | Crimson Rose — fatal errors, deletions, critical failures |
| `COLOR_MUTED` | `#64748B` | Slate Gray — secondary details, timestamps, IDs |
| `COLOR_CARD` | `#1E293B` | Midnight Blue — panel surfaces |
| `COLOR_VIOLET` | `#8B5CF6` | Neural Purple — AI models, inference, embeddings |

---

## 3. Shared UI Primitives

All UI components are centralized in `src/molab_cli/theme.py`:

### Status Badges
* `render_status_badge(is_running: bool, is_free: Optional[bool])`:
  * `[bold bright_green]🟢 ★ FREE[/bold bright_green]` (Idle Blackwell pod recommended for jobs)
  * `[bold yellow]🟡 ⚠️ OCCUPIED[/bold yellow]` (Pod actively running models or workloads)
  * `[bold green]🟢 RUNNING[/bold green]` (Container active)
  * `[dim]⚪ STOPPED[/dim]` (Container shut down)

### Hardware Badges
* `render_hw_badge(gpu: str)`:
  * `[bold #76b900]⚡ Blackwell 96GB[/bold #76b900]`
  * `[cyan]🖥️ 4 vCPU / 32GB[/cyan]`

### Notification & Status Cards
* `render_success_card(title, message)`: Emerald border with celebratory title.
* `render_error_card(title, message, hint=None, code=None)`: Crimson border with sanitized message and actionable remediation box.
* `render_warning_card(title, message)`: Amber border for cautionary updates.
* `render_info_card(title, message)`: Cyan border for instructions and summaries.
* `render_page_header(title, subtitle=None)`: Styled top banner for views.

---

## 4. Standard Page Composition

Interactive screens follow a consistent 5-tier layout:
```
+-------------------------------------------------------------------------------+
| ⚡ MoLab CLI  •  v2.4.0  •  Blackwell 96GB GDDR7 (sm_120)                     |
+-------------------------------------------------------------------------------+
| ● user@domain.com    │    ⚡ 1 Pod Active (★ Free: nb_emu...)   │   Blackwell  |
+-------------------------------------------------------------------------------+
|                                                                               |
|   [Main Actionable Content / Table / Telemetry Cards]                         |
|                                                                               |
+-------------------------------------------------------------------------------+
| ? Select action: (Use arrow keys, Enter to confirm, Esc to cancel)            |
|   ❯ 🚀 1-Click Deploy Production Model                                        |
|     💬 Terminal Coding Agent                                                  |
|     🌐 Start Unified Blackwell AI Bridge                                      |
|     ⬅️ Back                                                                   |
+-------------------------------------------------------------------------------+
```

---

## 5. Intelligent Target Auto-Resolution

Users should never be forced to copy-paste resource IDs when the application can infer or prompt for them:

1. **Explicit Argument:** If the user specifies `molab gpu nb_123`, the CLI immediately targets `nb_123` and records it as the active session pod.
2. **Omitted in Single-Pod Workspace:** If the user runs `molab gpu` or `molab forward` and exactly one pod is active, the CLI automatically selects it.
3. **Omitted in Multi-Pod Workspace:** In an interactive terminal (`isatty()`), the CLI presents an interactive selector (`pick_notebook`). In a non-interactive pipe, it exits cleanly with a helpful error.
4. **Offline Pods:** If no pods are active, commands requiring compute prompt or fail with a clear remediation command (`molab compute <id> --blackwell`).

---

## 6. Terminal Constraints & Pipe Safety

* **Non-Interactive Detection (`sys.stdin.isatty()`):** Spinners and interactive prompts are automatically bypassed when stdout or stdin is piped.
* **Pure JSON Mode (`-j, --json`):** Any command invoked with `--json` outputs raw, unadorned JSON to stdout without ANSI escape codes, status spinners, or decorative text.
* **Secret Redaction:** Cookies (`__client`), session tokens, RS256 JWTs, and virtual API keys (`sk-molab-...`) are automatically sanitized before any card or log output is printed.
* **Signal Handling:** `Ctrl+C` (SIGINT) cleanly restores terminal line buffering (`echo`, `canonical mode`) without corrupting terminal state.
