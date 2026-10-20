import Foundation
import CryptoKit

struct ClaudeTrustPrompt {
    let screen: String
    let selected: String
    let choices: [String]

    // Current screen only. A quoted prompt in a transcript is not an approval.
    static func parse(_ screen: String, directory: String) -> ClaudeTrustPrompt? {
        guard screen.count <= 20_000, !directory.isEmpty else { return nil }
        let lines = screen.components(separatedBy: .newlines)
            .map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        guard let start = lines.lastIndex(of: "Accessing workspace:"),
              lines.filter({ $0 == "Accessing workspace:" }).count == 1 else { return nil }
        let prompt = Array(lines[start...])
        let prose = prompt.dropFirst(2).dropLast(3).joined(separator: " ")
        guard prompt.count >= 8, prompt[1] == directory,
              prose.hasPrefix("Quick safety check: Is this a project you created or one you trust?"),
              prose.contains("Claude Code'll be able to read, edit, and execute files here."),
              prompt.last == "Enter to confirm · Esc to cancel" else { return nil }
        let choices = Array(prompt.dropLast().suffix(2))
        let labels = choices.map { $0.hasPrefix("❯ ") ? String($0.dropFirst(2)) : $0 }
        guard Set(labels) == Set(["No, exit", "Yes, I trust this folder"]),
              choices.filter({ $0.hasPrefix("❯ ") }).count == 1 else { return nil }
        return ClaudeTrustPrompt(screen: screen,
            selected: labels[choices.firstIndex(where: { $0.hasPrefix("❯ ") })!], choices: labels)
    }

    func keys(action: String) throws -> String {
        let desired: String
        switch action {
        case "accept": desired = "Yes, I trust this folder"
        case "decline": desired = "No, exit"
        default: throw PilotError(message: "不支援這個資料夾確認選項。")
        }
        guard let from = choices.firstIndex(of: selected), let to = choices.firstIndex(of: desired) else {
            throw PilotError(message: "資料夾確認選項已改變，未送出。")
        }
        // Terminal's do script adds Return. Never inject a generic text reply.
        return from == to ? "" : from < to ? "\u{1b}[B" : "\u{1b}[A"
    }

    static func identity(event: AgentEvent, tab: TerminalTab) -> String {
        let data = try! JSONEncoder().encode([event.id, tab.id, event.cwd, String(event.createdAt)])
        return "claude-trust:" + SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    func card(id: String, directory: String, state: String) -> [String: Any] {
        let original = screen.range(of: "Accessing workspace:").map {
            String(screen[$0.lowerBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
        } ?? ""
        return ["id": id, "kind": "workspace_trust", "state": state,
         "title": "允許 Claude 使用這個資料夾？",
         "detail": "同意後，Claude 可在這個資料夾讀取、修改與執行檔案，並載入專案設定。Claude 會記住此資料夾的信任選擇。",
         "context": [["label": "專案資料夾", "text": directory],
                     ["label": "Claude 原始確認", "text": original]],
         "actions": ["accept", "decline"], "fields": []]
    }
}

struct ClaudeTrustReceipt: Codable {
    var requestID: String
    var action: String
    var state: String // uncertain is persisted before the side effect
}

extension TerminalBridge {
    private static func claudeScreen(_ tab: TerminalTab) -> String {
        // `contents of t` dereferences the AppleScript reference variable t; it
        // does not read Terminal's contents property. Address the tab directly.
        "contents of (first tab of window id \(tab.windowID) whose tty is \(quote(tab.tty)))"
    }
    static func claudeTrust(_ tab: TerminalTab, agentPID: Int, directory: String) async throws -> ClaudeTrustPrompt? {
        guard let screen = try await target(tab, commands: "return \(claudeScreen(tab))").stringValue else {
            throw PilotError(message: "無法取得 Claude 的目前畫面，未送出任何按鍵。")
        }
        guard let prompt = ClaudeTrustPrompt.parse(screen, directory: directory),
              try await foregroundAgent(tab, agentPID: agentPID, tool: "claude") else { return nil }
        return prompt
    }

    static func answerClaudeTrust(_ tab: TerminalTab, agentPID: Int, directory: String, action: String) async throws {
        guard let prompt = try await claudeTrust(tab, agentPID: agentPID, directory: directory) else {
            throw PilotError(message: "Claude 已不在這個資料夾確認畫面，未送出任何按鍵。")
        }
        let keys = try prompt.keys(action: action)
        try await target(tab, commands: """
        if not busy of t then error "Claude 已離開，未送出。"
        if \(claudeScreen(tab)) is not \(quote(prompt.screen)) then error "確認畫面已改變，未送出。"
        do script \(quote(keys)) in t
        """)
    }
}
