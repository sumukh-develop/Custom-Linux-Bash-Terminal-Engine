import "./style.css";

type InitMessage = {
  type: "init";
  data: {
    session_id: string;
    prompt: string;
  };
};

type ResponseMessage = {
  type: "response";
  data: {
    output?: string;
    prompt?: string;
  };
};

type PingMessage = {
  type: "ping";
};

type NanoMessage = {
  type: "nano";
  data: {
    filename: string;
    path: string;
    content: string;
  };
};

type ServerMessage = InitMessage | ResponseMessage | PingMessage | NanoMessage;

type ClientCommand =
  | { type: "command"; data: string }
  | { type: "submit" }
  | { type: "pong" }
  | { type: "mode"; data: string }
  | { type: "nano_save"; data: { file: string; content: string } };

const SESSION_STORAGE_KEY = "terminal-engine.session-id";
const MAX_HISTORY = 100;
const DEV_FALLBACK_WS_URL = "ws://localhost:4041/terminal";
const CMD_HOME_LABEL = "C:\\Users\\Sumukh";
const CMD_BANNER = [
  "Microsoft Windows [Version 10.0.26200.8457]",
  "(c) Microsoft Corporation. All rights reserved.",
];

const app = document.querySelector<HTMLDivElement>("#app");

if (!app) {
  throw new Error("Missing app root");
}

class TerminalFrontEnd {
  private readonly root: HTMLDivElement;
  private readonly outputPane: HTMLDivElement;
  private readonly inputField: HTMLInputElement;
  private readonly promptField: HTMLSpanElement;
  private readonly statusField: HTMLSpanElement;
  private readonly sessionField: HTMLSpanElement;
  private readonly commandDisplay: HTMLSpanElement;
  private readonly modal: HTMLDivElement;
  private readonly nanoTitle: HTMLHeadingElement;
  private readonly nanoPath: HTMLParagraphElement;
  private readonly nanoEditor: HTMLTextAreaElement;
  private readonly nanoSaveButton: HTMLButtonElement;
  private readonly nanoCancelButtons: HTMLButtonElement[];
  private readonly nanoBackdrop: HTMLDivElement;

  private websocket: WebSocket | null = null;
  private reconnectTimer: number | null = null;
  private reconnectDelayMs = 1000;
  private sessionId = window.localStorage.getItem(SESSION_STORAGE_KEY) ?? "";
  private prompt = "user@server:~$ ";
  private history: string[] = [];
  private historyIndex = -1;
  private nanoState: NanoMessage["data"] | null = null;
  private closingForReconnect = false;
  private displayPrompt = `${CMD_HOME_LABEL}>`;

  constructor(root: HTMLDivElement) {
    this.root = root;
    this.root.innerHTML = "";
    this.root.appendChild(this.buildShell());
    this.outputPane = this.root.querySelector<HTMLDivElement>("[data-output]")!;
    this.inputField = this.root.querySelector<HTMLInputElement>("[data-input]")!;
    this.promptField = this.root.querySelector<HTMLSpanElement>("[data-prompt]")!;
    this.statusField = this.root.querySelector<HTMLSpanElement>("[data-status]")!;
    this.sessionField = this.root.querySelector<HTMLSpanElement>("[data-session]")!;
    this.commandDisplay = this.root.querySelector<HTMLSpanElement>("[data-command-display]")!;
    this.modal = this.root.querySelector<HTMLDivElement>("[data-nano-modal]")!;
    this.nanoTitle = this.root.querySelector<HTMLHeadingElement>("[data-nano-title]")!;
    this.nanoPath = this.root.querySelector<HTMLParagraphElement>("[data-nano-path]")!;
    this.nanoEditor = this.root.querySelector<HTMLTextAreaElement>("[data-nano-editor]")!;
    this.nanoSaveButton = this.root.querySelector<HTMLButtonElement>("[data-nano-save]")!;
    this.nanoCancelButtons = Array.from(this.root.querySelectorAll<HTMLButtonElement>("button[data-nano-cancel]"));
    this.nanoBackdrop = this.root.querySelector<HTMLDivElement>("[data-nano-backdrop]")!;

    this.wireEvents();
    this.syncCommandDisplay();
    for (const line of CMD_BANNER) {
      this.appendSystemLine(line);
    }
    this.connect();
    this.inputField.focus();
  }

  private buildShell(): HTMLDivElement {
    const shell = document.createElement("div");
    shell.className = "shell";
    shell.innerHTML = `
      <header class="shell__topbar">
        <div class="shell__controls" aria-hidden="true">
          <span></span>
        </div>
        <div class="shell__title">
          <strong>Command Prompt</strong>
          <span>Terminal Engine</span>
        </div>
        <div class="shell__meta">
          <span data-status>connected</span>
          <span data-session>session loading</span>
        </div>
      </header>
      <main class="shell__body">
        <section class="terminal" aria-label="Terminal output">
          <div class="terminal__output" data-output></div>
          <form class="terminal__input-row" autocomplete="off">
            <div class="terminal__composer">
              <span class="terminal__prompt" data-prompt>user@server:~$ </span>
              <div class="terminal__commandline" aria-hidden="true">
                <span class="terminal__commandtext" data-command-display></span>
                <span class="terminal__cursor" aria-hidden="true"></span>
              </div>
              <input
                data-input
                class="terminal__input"
                type="text"
                spellcheck="false"
                autocapitalize="off"
                autocomplete="off"
                autocorrect="off"
                placeholder="Type a command..."
                aria-label="Terminal command input"
              />
            </div>
          </form>
        </section>
      </main>
      <div class="nano-modal" data-nano-modal hidden>
        <div class="nano-modal__backdrop" data-nano-backdrop></div>
        <section class="nano-modal__panel" role="dialog" aria-modal="true" aria-labelledby="nano-title">
          <header class="nano-modal__header">
            <div>
              <p class="nano-modal__eyebrow">nano editor</p>
              <h2 id="nano-title" data-nano-title>Editing file</h2>
              <p class="nano-modal__path" data-nano-path></p>
            </div>
            <button class="nano-modal__close" type="button" data-nano-cancel aria-label="Close editor">x</button>
          </header>
          <textarea data-nano-editor class="nano-modal__editor" spellcheck="false"></textarea>
          <div class="nano-modal__actions">
            <button type="button" class="nano-modal__secondary" data-nano-cancel>Cancel</button>
            <button type="button" class="nano-modal__primary" data-nano-save>Save</button>
          </div>
          <p class="nano-modal__hint">Ctrl+Enter saves. Esc closes the editor.</p>
        </section>
      </div>
    `;
    return shell;
  }

  private wireEvents(): void {
    this.root.addEventListener("click", (event) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest(".nano-modal")) {
        return;
      }
      if (this.nanoState) {
        return;
      }
      this.inputField.focus();
    });

    this.inputField.addEventListener("keydown", (event) => {
      if (this.nanoState) {
        return;
      }

      if (event.key === "Enter") {
        event.preventDefault();
        this.submitCommand(this.inputField.value);
        return;
      }

      if (event.key === "ArrowUp") {
        event.preventDefault();
        this.stepHistory(-1);
        return;
      }

      if (event.key === "ArrowDown") {
        event.preventDefault();
        this.stepHistory(1);
        return;
      }

      if (event.ctrlKey && event.key.toLowerCase() === "l") {
        event.preventDefault();
        this.clearScreen();
      }
    });

    this.inputField.addEventListener("input", () => {
      this.historyIndex = this.history.length;
      this.syncCommandDisplay();
    });

    this.root.addEventListener("submit", (event) => {
      event.preventDefault();
      this.submitCommand(this.inputField.value);
    });

    this.nanoSaveButton.addEventListener("click", () => {
      this.saveNano();
    });

    for (const button of this.nanoCancelButtons) {
      button.addEventListener("click", () => {
        this.closeNano();
      });
    }

    this.nanoBackdrop.addEventListener("click", () => {
      this.closeNano();
    });

    this.nanoEditor.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        this.closeNano();
      }

      if (event.ctrlKey && event.key === "Enter") {
        event.preventDefault();
        this.saveNano();
      }
    });

    window.addEventListener("beforeunload", () => {
      this.closingForReconnect = true;
      this.websocket?.close();
    });
  }

  private connect(): void {
    const url = this.buildWebSocketUrl();
    this.setStatus("connecting");

    const websocket = new WebSocket(url);
    this.websocket = websocket;

    websocket.addEventListener("open", () => {
      this.reconnectDelayMs = 1000;
      this.setStatus("connected");
    });

    websocket.addEventListener("message", (event) => {
      this.handleMessage(event.data);
    });

    websocket.addEventListener("close", () => {
      this.setStatus("offline");

      if (!this.closingForReconnect) {
        this.scheduleReconnect();
      }
    });

    websocket.addEventListener("error", () => {
      this.setStatus("error");
    });
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer !== null) {
      return;
    }

    const delay = this.reconnectDelayMs;
    this.reconnectDelayMs = Math.min(this.reconnectDelayMs * 2, 10_000);

    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);

    this.appendSystemLine(`Reconnecting in ${Math.round(delay / 1000)}s...`);
  }

  private buildWebSocketUrl(): string {
    const stored = import.meta.env.VITE_WEBSOCKET_URL?.trim();
    const baseUrl =
      stored && stored.length > 0
        ? stored
        : window.location.port === "5173"
          ? DEV_FALLBACK_WS_URL
          : `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/terminal`;

    const sessionId = this.sessionId.trim();
    if (!sessionId) {
      return baseUrl;
    }

    const connector = baseUrl.includes("?") ? "&" : "?";
    return `${baseUrl}${connector}session_id=${encodeURIComponent(sessionId)}`;
  }

  private handleMessage(raw: string): void {
    let message: ServerMessage;

    try {
      message = JSON.parse(raw) as ServerMessage;
    } catch {
      this.appendSystemLine("Received invalid server message.");
      return;
    }

    if (message.type === "ping") {
      this.send({ type: "pong" });
      return;
    }

    if (message.type === "init") {
      this.sessionId = message.data.session_id;
      window.localStorage.setItem(SESSION_STORAGE_KEY, this.sessionId);
      this.prompt = message.data.prompt;
      this.displayPrompt = this.formatCmdPrompt(message.data.prompt);
      this.updatePrompt();
      this.sessionField.textContent = `session ${this.sessionId.slice(0, 8)}`;
      return;
    }

    if (message.type === "nano") {
      this.openNano(message.data);
      return;
    }

    const output = message.data.output ?? "";
    if (output.length > 0) {
      this.appendOutputBlock(output);
    }

    if (message.data.prompt) {
      this.prompt = message.data.prompt;
      this.displayPrompt = this.formatCmdPrompt(message.data.prompt);
      this.updatePrompt();
    }
  }

  private send(message: ClientCommand): void {
    if (!this.websocket || this.websocket.readyState !== WebSocket.OPEN) {
      this.appendSystemLine("Not connected yet.");
      return;
    }

    this.websocket.send(JSON.stringify(message));
  }

  private submitCommand(rawCommand: string): void {
    const command = rawCommand.trim();
    if (!command || this.nanoState) {
      this.inputField.value = "";
      this.syncCommandDisplay();
      return;
    }

    this.history.push(command);
    if (this.history.length > MAX_HISTORY) {
      this.history.shift();
    }
    this.historyIndex = this.history.length;

    this.appendCommandLine(this.prompt, rawCommand);
    this.inputField.value = "";
    this.syncCommandDisplay();

    if (this.isSubmitCommand(command)) {
      this.send({ type: "submit" });
      return;
    }

    if (command === "clear") {
      this.clearScreen();
      return;
    }

    if (command === "cls") {
      this.clearScreen();
      return;
    }

    if (command.startsWith("mode ")) {
      this.send({
        type: "mode",
        data: command.slice("mode ".length).trim(),
      });
      return;
    }

    this.send({ type: "command", data: rawCommand });
  }

  private isSubmitCommand(command: string): boolean {
    return command === "submit" || command === "\\submit" || command === "/submit" || command === ":submit";
  }

  private stepHistory(direction: -1 | 1): void {
    if (!this.history.length) {
      return;
    }

    this.historyIndex = Math.max(0, Math.min(this.history.length, this.historyIndex + direction));

    if (this.historyIndex === this.history.length) {
      this.inputField.value = "";
      this.syncCommandDisplay();
      return;
    }

    this.inputField.value = this.history[this.historyIndex] ?? "";
    this.syncCommandDisplay();
    window.requestAnimationFrame(() => {
      this.inputField.setSelectionRange(this.inputField.value.length, this.inputField.value.length);
    });
  }

  private openNano(data: NanoMessage["data"]): void {
    this.nanoState = data;
    this.nanoTitle.textContent = data.filename;
    this.nanoPath.textContent = data.path;
    this.nanoEditor.value = data.content;
    this.modal.hidden = false;
    this.inputField.disabled = true;
    window.setTimeout(() => this.nanoEditor.focus(), 0);
  }

  private closeNano(): void {
    this.nanoState = null;
    this.modal.hidden = true;
    this.inputField.disabled = false;
    this.inputField.focus();
  }

  private saveNano(): void {
    if (!this.nanoState) {
      return;
    }

    this.send({
      type: "nano_save",
      data: {
        file: this.nanoState.filename,
        content: this.nanoEditor.value,
      },
    });
    this.closeNano();
  }

  private updatePrompt(): void {
    this.promptField.textContent = this.displayPrompt;
  }

  private syncCommandDisplay(): void {
    this.commandDisplay.textContent = this.inputField.value;
  }

  private formatCmdPrompt(prompt: string): string {
    const cwd = this.extractPromptPath(prompt);
    const normalized = this.normalizeCmdPath(cwd);
    return `${normalized}>`;
  }

  private extractPromptPath(prompt: string): string {
    const colonIndex = prompt.indexOf(":");
    const dollarIndex = prompt.lastIndexOf("$");
    if (colonIndex === -1 || dollarIndex === -1 || dollarIndex <= colonIndex) {
      return prompt.trim().replace(/\s+/g, " ");
    }

    return prompt.slice(colonIndex + 1, dollarIndex).trim();
  }

  private normalizeCmdPath(path: string): string {
    if (!path || path === "~") {
      return CMD_HOME_LABEL;
    }

    if (path.startsWith("~/")) {
      return `${CMD_HOME_LABEL}\\${path.slice(2).replaceAll("/", "\\")}`;
    }

    if (path.startsWith("/home/user")) {
      const suffix = path.slice("/home/user".length).replaceAll("/", "\\");
      return `${CMD_HOME_LABEL}${suffix}`;
    }

    return path.replaceAll("/", "\\");
  }

  private setStatus(status: string): void {
    this.statusField.textContent = status;
    this.root.dataset.status = status;
  }

  private appendCommandLine(prompt: string, command: string): void {
    const line = document.createElement("div");
    line.className = "terminal__line terminal__line--command";

    const promptSpan = document.createElement("span");
    promptSpan.className = "terminal__prompt";
    promptSpan.textContent = prompt;

    const commandSpan = document.createElement("span");
    commandSpan.className = "terminal__command";
    commandSpan.textContent = command;

    line.append(promptSpan, commandSpan);
    this.outputPane.appendChild(line);
    this.scrollToBottom();
  }

  private appendOutputBlock(text: string): void {
    const line = document.createElement("pre");
    line.className = "terminal__line terminal__line--output";
    line.textContent = text;
    this.outputPane.appendChild(line);
    this.scrollToBottom();
  }

  private appendSystemLine(text: string): void {
    const line = document.createElement("div");
    line.className = "terminal__line terminal__line--system";
    line.textContent = text;
    this.outputPane.appendChild(line);
    this.scrollToBottom();
  }

  private clearScreen(): void {
    this.outputPane.innerHTML = "";
  }

  private scrollToBottom(): void {
    this.outputPane.scrollTop = this.outputPane.scrollHeight;
  }
}

new TerminalFrontEnd(app);
