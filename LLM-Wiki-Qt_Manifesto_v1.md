# LLM-Wiki-Qt Project

## Project Goals

To provide an authoritative, high-density technical reference for a user defined LLM Wiki Knowledge Base—built with Python, PySide6, and Qt Designer, and powered locally by llama.cpp—all source data and conversation history are synthesized into five distinct parts. These parts are architected according to Generative Engine Optimization (GEO) standards, utilizing single-intent headers and granular technical descriptions to ensure maximum retrieval accuracy for AI agents.

### Part 1: The LLM Wiki Framework and GEO Standards

This section serves as the "operational soul" for knowledge management within the repository.

- **Three-Layer Architecture**: The wiki utilizes a modular structure for technical provenance:
  - **Layer 1: Raw Sources (`raw/`)**: Immutable storage for original raw source documentation
  - **Layer 2: Structured Wiki (`wiki/`)**: Active knowledge layer managed by the agent, containing `sources/` (summaries of raw informational inputs), `entities/` (profiles of core subjects, figures, or items), `concepts/` (foundational principles and theme breakdowns), and `synthesis/` (connections and overarching insights across the domain)
  - **Layer 3: Operational Schema (SCHEMA.md)**: Defines strict rules for ingestion, querying, and linting
- **Operational Workflows**:

  - `/wiki-ingest` (Ingestion & Compiling Pipeline)

    *Multi-step sequence executing the following operations in order*:

    - **Queueing**: Places incoming user-defined knowledge inputs into a chronological processing queue.
    - **Compiling Ingested Data**: Standardizes, parses, and bundles queued raw materials into structured informational assets.
    - **Source Analysis**: Reviews compiled data to extract core themes, figures, and concepts.
    - **Summary Generation**: Creates clean, semantic overviews of the newly added assets.
    - **Surgical Text Modification**: Targets and updates specific text segments across existing pages to integrate new data smoothly.

  - `/wiki-link` (Network Construction & Mapping)

    *Automated mapping sequence executed immediately following ingestion to build a highly navigable knowledge web*:

    - **Source Linking**: Anchors newly compiled summaries directly back to their original reference documents.
    - **Backlinking**: Scans the entire knowledge base to automatically generate bi-directional references between related pages.
    - **Context & Semantic Linking**: Maps structural and conceptual relationships between subjects, strictly limited to a maximum of 3 degrees of separation to prevent contextual drift.

  - `/wiki-lint` (System Health & Integrity)

    *Periodic background checks to maintain the health and consistency of the user's knowledge base*:

    - **Deterministic Integrity**: Uses structural pattern matching to instantly locate and flag broken internal links.
    - **Semantic Consistency**: Analyzes the knowledge network to detect factual contradictions or conflicting information across related topics.

- **Generative Engine Optimization (GEO)**:
  - **Format Efficiency**: Structured Markdown reduces token overhead by up to 90% compared to HTML
  - **Atomic Chunking**: Pages focus on a single intent with section lengths strictly between 200–400 words to align with vector embedding windows
  - **Semantic Anchoring**: Descriptive, action-oriented headers act as predictable deep-link anchors
  - **Epistemic Markers**: Statements are tagged to clarify authorship: `` for AI synthesis, [P] for developer position, and `[?]` for unverified patterns

### Part 2: Application Framework

- **Core Technology Stack**
  - **Logic Layer**: Python-based application runtime.
  - **UI/UX Engine**: PySide6 (Qt for Python) leveraging native widgets for cross-platform stability.
  - **Visual Layout Blueprint**: Designed visually via Qt Designer (.ui file architecture) to separate UI structure from operational backend logic.

- **Modular Workspace UI (Docked Architecture)**
  - **User-Customizable Layouts**: Fully draggable, floatable, and nestable QDockWidgets allowing users to customize their ideal knowledge environment.
  - **Integrated Terminal Emulator**: Native embedded terminal panel residing in a dedicated dock widget for low-level system interaction and execution monitoring.
  - **Ingestion Queue Monitor**: Real-time dock panel displaying the chronological list of pending informational assets currently waiting in the processing queue.
  - **Active Pipeline Progress Log**: High-density logging dock widget printing live updates, compilation milestones, and network construction steps.
  - **System Health Dashboard**: Dedicated status dock reporting on the wiki's overall integrity, broken link statuses, and detected semantic contradictions.
  - **Manual Pipeline Control Center**: Interaction panel equipped with granular command buttons, giving users step-by-step, manual execution control over individual ingestion and linking stages.
  - **Interactive AI Chatbot Dock**: A dedicated conversational interface used for direct wiki querying, rapid testing, or general-purpose chat workflows.
  - **Integrated Git Versioning Control**: A dedicated repository management panel giving users immediate version control over their entire knowledge base filesystem.

- **Git Lifecycle Operations**
  - **Repository Initialization**: One-click setup (`git init`) to instantly begin tracking a newly created knowledge domain.
  - **Remote GitHub Linking**: Ability to dynamically set, update, and authenticate a remote GitHub target repository URL (`git remote add origin`).
  - **State Management & Commits**: Simple interface to stage altered files, write change messages, and snapshot the knowledge base (`git commit`).
  - **Remote Synchronization**: Seamless upstream integration allowing users to fetch changes (`git pull`), upload local modifications (`git push`), or trigger a complete bidirectional update (`git sync`).

- **System Configuration & Preferences**
  - **Application Menu Bar**: Standard top-level window menu housing universal view toggles, tool access, and configuration layouts.
  - **Settings & Preferences Window**: A dedicated modal interface triggered from the menu bar to manage llama.cpp connection profiles, specifically exposing customizable fields for IP addresses, server ports, and base API endpoints.

- **Data Persistence & Chat Lifecycle**
  - **Markdown-Based History Logging**: Automatic background saving of chat interaction transcripts directly into clean, standardized Markdown format files (`.md`).
  - **On-Demand Chat Ingestion**: User-directed workflow enabling saved conversation history logs to be pushed directly back into the ingestion queue for semantic compilation, cross-linking, and expansion of the permanent wiki network.

- **Dynamic Data Visualization (Central Hub)**
  - **Central Workspace Display**: Core canvas dedicated to an interactive, real-time spatial knowledge network graph (inspired by the Obsidian.md model).
  - **Live Graph Population**: Animated network node generation that visibly populates, updates, clusters, and maps new links live as documents progress from raw ingestion to 3-degree semantic compilation.

### Part 3: Model Context Protocol (MCP) Framework

- **Core Technology Stack**
  - **Protocol Core**: Built on the open Model Context Protocol standard to expose local tools, resources, and prompts to external LLM clients.
  - **Development Engine**: Implemented via the FastMCP framework for Python to provide high-performance, asynchronous service delivery.
  - **Transport Layer**: Configurable server infrastructure running over Server-Sent Events (SSE) or standard input/output (stdio) pipelines.

- **Server Lifecycle Management UI**
  - **Dedicated Control Interface**: A modular dock widget or standalone configuration panel for direct control of the local MCP environment.
  - **Operational States**: One-click, user-driven actions to instantly Start, Stop, or Restart the local protocol server.
  - **Network & Address Configuration**: Granular connection inputs within the main application Settings Window to manually define the target IP address and listening port for the server.

- **Exposed Agentic Capabilities (Tool & Resource Registry)**
  - **Semantic Search Engine**: High-fidelity search tools allowing external AI clients to execute similarity queries across the compiled sources/ and concepts/ directories.
  - **Entity Profile Retrieval**: Structural lookup functions giving agents direct access to raw profiles, node files, and context maps inside the entities/ layer.
  - **Network Path Traversal**: Path-finding utilities allowing an external LLM to trace connections between subjects up to the strict 3 degrees of separation limit.
  - **Cross-System Synthesis Reads**: High-level data streaming that serves consolidated insights and cross-system relationships found in the synthesis/ directory.

- **Cross-Application Interoperability**
  - **Universal Client Compatibility**: Full support for any external chat ecosystem or developer workspace that accepts custom MCP servers (e.g., Anthropic Claude Desktop, Cursor, Zed, or custom web interfaces).
  - **Security & Local Sandboxing**: Restricts file reading and editing privileges exclusively to the user-defined domain workspace, protecting the host system from arbitrary execution.

## Project Environment

**The following is a list of the current environment:**

1. **PROJECT LOCATION**: `/home/phil/pyDev/projects/LLM-Wiki-Qt/`

    **CURRENT FILES** *see `project_2026-07-26.md` for up-to-date files*

    | Files                | Type           |
    | -------------------- | -------------- |
    | atomizer.py          | python         |
    | compiler_engine.py   | python         |
    | config_manager.py    | python         |
    | dialog_new_vault.py  | python         |
    | dialog_settings.py   | python         |
    | git_manager.py       | python         |
    | graph_widget.py      | python         |
    | ingest_engine.py     | python         |
    | konsole_widget.py    | python         |
    | link_engine.py       | python         |
    | lint_engine.py       | python         |
    | logger_config.py     | python         |
    | main.py              | python         |
    | mcp_manager.py       | python         |
    | mcp_server.py        | python         |
    | MainWindow.ui        | Qt Designer UI |
    | SettingsDialog.ui    | Qt Designer UI |
    | ui_MainWindow.py     | python         |
    | ui_SettingsDialog.py | python         |
    | vault_manager.py     | python         |

2. **PYTHON VIRTUAL ENVIRONMENT**: `/home/phil/pyDev/venv/`

    **INSTALLED MODULES**:

    | Package            | Version |
    | ------------------ | ------- |
    | ansi2html          | 1.9.2   |
    | loguru             | latest  |
    | FastMCP            | latest  |
    | networkx           | latest  |
    | packaging          | 26.2    |
    | pip                | 26.1.2  |
    | Pydantic           | latest  |
    | PyQt6              | 6.11.0  |
    | PyQt6-Qt6          | 6.11.1  |
    | PyQt6_sip          | 13.11.1 |
    | PySide6            | 6.11.1  |
    | PySide6_Addons     | 6.11.1  |
    | PySide6_Essentials | 6.11.1  |
    | pyte               | 0.8.2   |
    | python-frontmatter | latest  |
    | pyyaml             | latest  |
    | qt-material        | latest  |
    | QtPy               | 2.4.3   |
    | qtpyTerminal       | 0.1     |
    | requests           | latest  |
    | shiboken6          | 6.11.1  |
    | wcwidth            | 0.8.2   |

3. **IDE ENVIRONMENT**: Visual Studio Code

   - **Workspace**: `/home/phil/pyDev/projects/LLM-Wiki-Qt/LLM-Wiki-Qt.code-workspace`
   - **Settings**: `/home/phil/pyDev/projects/LLM-Wiki-Qt/.vscode/settings.json`
   - **Tasks**: `/home/phil/pyDev/projects/LLM-Wiki-Qt/.vscode/tasks.json`
     - Used to compile `.ui` files into `python` using `ctrl+shft+b`

    **EXTENSION**

    | Extension                       |
    | ------------------------------- |
    | Black Formatter                 |
    | Chat Customizations Evaluations |
    | Copilot for llama-server LLMs   |
    | Foam                            |
    | Markdown All in One             |
    | Markdown Preview Enhanced       |
    | Prettier - Code formatter       |
    | Pylance                         |
    | Python                          |
    | Python Debugger                 |
    | Python Environments             |
    | Qt Core                         |
    | Qt Python                       |
    | Qt Python Extension Pack        |
    | Qt Qml                          |
    | Qt UI                           |
    | Rainbow CSV                     |

4. **UI DESIGN ENVIRONMENT**: Qt Designer 6

   - All `UI` design is done using Qt Designer 6 in a visual way.
   - No designs  will be implemented programmatically other then using `tasks.json` to compile `.ui` files into `.py`

5. **LLM ENVIRONMENT**:

   - **Provider**: llama.cpp
   - **Mode**: llama-server routed mode
   - **Service**: llama-cluster.service
   - **Models**:

    | Model Name              | Model                                   | CTX Size |
    | ----------------------- | --------------------------------------- | -------- |
    | hermes3-64k-latest      | Hermes-3-Llama-3.1-8B-Q4_K_M.gguf       | 65536    |
    | gemma3-4b               | gemma-3-4b-it-Q4_K_M.gguf               | 32768    |
    | qwen3-8b                | Qwen3-8B-Q4_K_M.gguf                    | 8192     |
    | llama3.2-3b             | Llama-3.2-3B-Instruct-Q5_K_M.gguf       | 4096     |
    | qwen2.5-coder-14b       | qwen2.5-coder-14b-instruct-q4_k_m.gguf  | 32768    |
    | qwen2.5-coder-7b        | Qwen2.5-Coder-7B-Instruct-Q5_K_M.gguf   | 8192     |
    | qwen2.5-coder-1.5b      | Qwen2.5-Coder-1.5B-Instruct-Q5_K_M.gguf | 8192     |
    | nomic-embed-text-latest | nomic-embed-text-v1.5.Q5_K_M.gguf       | 2048     |
    | Qwen3-Reranker          | Qwen3-Reranker-4B.Q5_K_M.gguf           | 4096     |
