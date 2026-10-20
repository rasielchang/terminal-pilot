import Foundation
import CryptoKit

/// Only used before Codex has a thread. Never parse agent conversation history.
struct StartupPrompt {
    let screen: String
    let text: String
    let choices: [String]
    let selected: Int
    let hooks: Bool

    static func parse(_ screen: String) -> StartupPrompt? {
        guard !screen.isEmpty, screen.count <= 20_000 else { return nil }
        let lines = screen.components(separatedBy: .newlines)
            .map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        guard let footer = lines.last?.lowercased(),
              footer.contains("enter"),
              footer.contains("confirm") || footer.contains("continue") || footer.contains("select") else { return nil }
        // A complete numbered menu, not arbitrary key injection. Keep wrapped
        // labels/descriptions and exclude the selection cursor from its identity.
        let pattern = try! NSRegularExpression(pattern: "^(?:(›|❯|>)\\s+)?([0-9]{1,2})\\.\\s+(.+)$")
        var choices: [String] = [], selected: [Int] = [], start: Int?
        var valid = true
        for (index, line) in lines.dropLast().enumerated() {
            if let match = pattern.firstMatch(in: line, range: NSRange(line.startIndex..., in: line)),
               let numberRange = Range(match.range(at: 2), in: line),
               let labelRange = Range(match.range(at: 3), in: line) {
                guard Int(line[numberRange]) == choices.count + 1 else { valid = false; break }
                if start == nil { start = index }
                if match.range(at: 1).location != NSNotFound { selected.append(choices.count) }
                choices.append(String(line[labelRange]))
            } else if start != nil {
                // Wrapped text cannot introduce another unparsed cursor/menu.
                if line.hasPrefix("›") || line.hasPrefix("❯") || line.hasPrefix(">") { valid = false; break }
                choices[choices.count - 1] += " " + line
            }
        }
        let hooks = lines.contains("Hooks need review")
        guard let start, start > 0, valid, (2...12).contains(choices.count), selected.count == 1 else {
            // Unknown/new CLI format is visible, but never guessed or approved.
            return StartupPrompt(screen: screen, text: lines.joined(separator: "\n"), choices: [], selected: 0, hooks: hooks)
        }
        let text = (Array(lines[..<start]) + choices.enumerated().map { "\($0.offset + 1). \($0.element)" } + [lines.last!]).joined(separator: "\n")
        return StartupPrompt(screen: screen, text: text, choices: choices, selected: selected[0], hooks: hooks)
    }

    var actions: [String] {
        choices.indices.filter { !(hooks && choices[$0] == "Review hooks") }.map { "choice_\($0 + 1)" }
    }
    func identity(event: AgentEvent, tab: TerminalTab) -> String {
        let data = try! JSONEncoder().encode([event.id, tab.id, event.cwd, String(event.createdAt), text])
        return "startup:" + SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
    func keys(action: String) throws -> String {
        guard actions.contains(action), let number = Int(action.dropFirst("choice_".count)) else {
            throw PilotError(message: "啟動確認選項已改變，未送出。")
        }
        let delta = number - 1 - selected
        return String(repeating: delta > 0 ? "\u{1b}[B" : "\u{1b}[A", count: abs(delta))
    }
    func card(id: String, directory: String, state: String) -> [String: Any] {
        var labels: [String: String] = [:]
        for (index, choice) in choices.enumerated() {
            let label: String
            switch choice {
            case "Trust all and continue": label = "信任以上 hooks 並繼續"
            case "Continue without trusting (hooks won't run)": label = "不信任並繼續（hooks 不會執行）"
            default: label = choice
            }
            labels["choice_\(index + 1)"] = label
        }
        return ["id": id, "kind": "startup_menu", "state": state,
            "startupType": hooks ? "hooks" : "menu",
            "title": hooks ? "Codex 等待你確認 hooks" : "Codex 等待啟動確認",
            "detail": hooks ? "這些 hooks 可在 sandbox 外執行。請查看定義後，再選擇是否信任。" : "請確認下方原始內容，再選擇要執行的操作。",
            "context": [["label": "專案資料夾", "text": directory], ["label": "原始確認", "text": text]],
            "actions": actions, "actionLabels": labels, "fields": [],
            "unsupported": choices.isEmpty ? "這個啟動畫面的格式尚無法安全辨識，請在電腦完成確認；手機未送出任何按鍵。" : ""]
    }
}

extension TerminalBridge {
    private static func startupScreen(_ tab: TerminalTab) -> String {
        "contents of (first tab of window id \(tab.windowID) whose tty is \(quote(tab.tty)))"
    }
    static func startupPrompt(_ tab: TerminalTab, agentPID: Int) async throws -> StartupPrompt? {
        guard let screen = try await target(tab, commands: "return \(startupScreen(tab))").stringValue,
              let prompt = StartupPrompt.parse(screen),
              try await foregroundAgent(tab, agentPID: agentPID, tool: "codex") else { return nil }
        return prompt
    }
    static func answerStartup(_ tab: TerminalTab, agentPID: Int, expected: String, action: String) async throws {
        guard let prompt = try await startupPrompt(tab, agentPID: agentPID), prompt.text == expected else {
            throw PilotError(message: "啟動畫面已改變，未送出。請查看最新確認。")
        }
        let keys = try prompt.keys(action: action)
        try await target(tab, commands: """
        if not busy of t then error "Codex 已離開，未送出。"
        if \(startupScreen(tab)) is not \(quote(prompt.screen)) then error "啟動畫面已改變，未送出。"
        do script \(quote(keys)) in t
        """)
    }
}
