/**
 * Nova VSCode Extension — diff gate + editor state server
 *
 * Starts an HTTP server on port 3333 when VSCode activates.
 * Nova's Python side (tools/vscode.py) talks to this server
 * to propose diffs and wait for Jashan's decision.
 *
 * Two responsibilities, cleanly separated:
 *   1. Diff gate  — /diff/show, /diff/status, nova.acceptDiff, nova.rejectDiff
 *   2. Editor state — /state, /health  (used by worker_env_snapshot, Fase futura)
 *
 * One diff at a time. If a second /diff/show arrives while one is
 * pending, it is rejected with 409 — the Director never sends two
 * show_diff steps in parallel (they are always in separate levels),
 * so this is a safety net, not a normal path.
 */

import * as vscode from "vscode";
import * as http from "http";
import * as fs from "fs";
import * as path from "path";

// ------------------------------------------------------------
// Diff state — module-level, one active diff at a time
// ------------------------------------------------------------

type DiffStatus = "idle" | "pending" | "accepted" | "rejected";

let diffStatus: DiffStatus = "idle";
let currentTmpPath: string | null = null;

// ------------------------------------------------------------
// HTTP server
// ------------------------------------------------------------

const PORT = 3333;
let server: http.Server;

function startServer(context: vscode.ExtensionContext): void {
    server = http.createServer((req, res) => {
        const url = req.url ?? "";
        const method = req.method ?? "";

        // --- POST /diff/show ---
        if (method === "POST" && url === "/diff/show") {
            if (diffStatus === "pending") {
                sendJson(res, 409, {
                    error: "A diff is already pending. Wait for Jashan to decide.",
                });
                return;
            }

            collectBody(req, (body) => {
                const { file_path, tmp_path } = body;

                if (!file_path || !tmp_path) {
                    sendJson(res, 400, {
                        error: "Both 'file_path' and 'tmp_path' are required.",
                    });
                    return;
                }

                if (!fs.existsSync(file_path)) {
                    sendJson(res, 400, {
                        error: `Original file not found: ${file_path}`,
                    });
                    return;
                }

                if (!fs.existsSync(tmp_path)) {
                    sendJson(res, 400, {
                        error: `Tmp file not found: ${tmp_path}`,
                    });
                    return;
                }

                // Set state before opening the editor — the poll may arrive
                // before the editor is fully open, and "pending" is the right
                // answer in that window.
                diffStatus = "pending";
                currentTmpPath = tmp_path;

                // Open VSCode Diff Editor: left = original, right = proposed
                const originalUri = vscode.Uri.file(file_path);
                const proposedUri = vscode.Uri.file(tmp_path);
                const title = `Nova: ${path.basename(file_path)} (proposed)`;

                vscode.commands.executeCommand(
                    "vscode.diff",
                    originalUri,
                    proposedUri,
                    title
                );

                sendJson(res, 200, { status: "pending" });
            });
            return;
        }

        // --- GET /diff/status ---
        if (method === "GET" && url === "/diff/status") {
            sendJson(res, 200, { status: diffStatus });
            return;
        }

        // --- GET /health ---
        if (method === "GET" && url === "/health") {
            sendJson(res, 200, { status: "ok", port: PORT });
            return;
        }

        // --- GET /state  (worker_env_snapshot — Fase futura) ---
        if (method === "GET" && url === "/state") {
            const state = getEditorState();
            sendJson(res, 200, state);
            return;
        }

        // --- 404 ---
        sendJson(res, 404, { error: `Unknown endpoint: ${method} ${url}` });
    });

    server.listen(PORT, "127.0.0.1", () => {
        vscode.window.setStatusBarMessage(`$(check) Nova server :${PORT}`, 5000);
    });

    server.on("error", (err: NodeJS.ErrnoException) => {
        if (err.code === "EADDRINUSE") {
            vscode.window.showErrorMessage(
                `Nova: port ${PORT} is already in use. ` +
                "Is another instance of the extension running?"
            );
        } else {
            vscode.window.showErrorMessage(`Nova server error: ${err.message}`);
        }
    });
}

// ------------------------------------------------------------
// Diff commands — registered as VSCode commands and bound to
// Ctrl+Shift+Y / Ctrl+Shift+N via package.json keybindings.
// The keybinding condition "isInDiffEditor" ensures these only
// fire when a diff is actually open — not in regular editors.
// ------------------------------------------------------------

function registerDiffCommands(context: vscode.ExtensionContext): void {
    context.subscriptions.push(
        vscode.commands.registerCommand("nova.acceptDiff", () => {
            if (diffStatus !== "pending") {
                // No active diff — ignore silently. Can happen if the user
                // presses Ctrl+Shift+Y in a non-Nova diff editor.
                return;
            }

            diffStatus = "accepted";
            // vscode.py handles the actual file write and tmp cleanup.
            // The extension only manages state and UI.
            closeActiveDiffEditor();
            resetDiffState();
        })
    );

    context.subscriptions.push(
        vscode.commands.registerCommand("nova.rejectDiff", () => {
            if (diffStatus !== "pending") {
                return;
            }

            diffStatus = "rejected";
            // vscode.py handles tmp cleanup on rejected path.
            closeActiveDiffEditor();
            resetDiffState();
        })
    );
}

// ------------------------------------------------------------
// Helpers
// ------------------------------------------------------------

function resetDiffState(): void {
    // Reset happens immediately after setting accepted/rejected —
    // the poll in vscode.py reads the decision on the next tick,
    // then the state is clean for the next diff. The short window
    // between "set decision" and "reset" is intentional: reset only
    // after the decision is committed to diffStatus, not before.
    // A tiny setTimeout avoids the race where reset fires before
    // vscode.py reads the non-idle status.
    setTimeout(() => {
        diffStatus = "idle";
        currentTmpPath = null;
    }, 5000); // 5s — well above the 2s poll interval in vscode.py
}

function closeActiveDiffEditor(): void {
    // Close the active tab if it is a diff editor. workbench.action.closeActiveEditor
    // works regardless of editor type — if the diff is active, it closes it.
    vscode.commands.executeCommand("workbench.action.closeActiveEditor");
}

function getEditorState(): object {
    const editor = vscode.window.activeTextEditor;
    const workspaceFolders = vscode.workspace.workspaceFolders;

    return {
        active_file: editor?.document.uri.fsPath ?? null,
        project: workspaceFolders?.[0]?.name ?? null,
        branch: null, // populated by worker_env_snapshot via git CLI, not here
        open_tabs: vscode.window.tabGroups.all
            .flatMap((g) => g.tabs)
            .map((t) => (t.input as any)?.uri?.fsPath)
            .filter(Boolean),
        language: editor?.document.languageId ?? null,
    };
}

function collectBody(
    req: http.IncomingMessage,
    callback: (body: any) => void
): void {
    let data = "";
    req.on("data", (chunk) => (data += chunk));
    req.on("end", () => {
        try {
            callback(JSON.parse(data));
        } catch {
            callback({});
        }
    });
}

function sendJson(res: http.ServerResponse, status: number, body: object): void {
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify(body));
}

// ------------------------------------------------------------
// Extension lifecycle
// ------------------------------------------------------------

export function activate(context: vscode.ExtensionContext): void {
    startServer(context);
    registerDiffCommands(context);
}

export function deactivate(): void {
    server?.close();
}