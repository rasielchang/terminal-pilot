import AppKit

struct TerminalTab: Identifiable, Codable {
    var tty: String
    var windowID: Int
    var title: String
    var minimized: Bool
    var bounds: [Int]
    var selected: Bool
    var terminalPID: Int
    var id: String { "\(terminalPID):\(windowID):\(tty)" }
}

struct PilotError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

enum TerminalBridge {
    private static let queue = DispatchQueue(label: "local.terminal-pilot.applescript")
    static func quote(_ value: String) -> String {
        "\"" + value.replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
            .replacingOccurrences(of: "\n", with: "\\n")
            .replacingOccurrences(of: "\r", with: "\\r") + "\""
    }
    static func shellQuote(_ value: String) -> String {
        "'" + value.replacingOccurrences(of: "'", with: "'\"'\"'") + "'"
    }
    static func execute(_ source: String) async throws -> NSAppleEventDescriptor {
        try await withCheckedThrowingContinuation { continuation in
            queue.async {
                var error: NSDictionary?
                guard let script = NSAppleScript(source: "with timeout of 8 seconds\n\(source)\nend timeout") else {
                    continuation.resume(throwing: PilotError(message: "無法建立 Terminal 控制指令。"))
                    return
                }
                let result = script.executeAndReturnError(&error)
                if let error = error {
                    let code = error[NSAppleScript.errorNumber] as? Int ?? 0
                    let detail = error[NSAppleScript.errorMessage] as? String ?? "未知錯誤"
                    let text = code == -1743
                        ? "請到系統設定 → 隱私權與安全性 → 自動化，允許 Terminal Pilot 控制 Terminal，再按重新連接。"
                        : "Terminal 控制失敗（\(code)）：\(detail)"
                    continuation.resume(throwing: PilotError(message: text))
                } else { continuation.resume(returning: result) }
            }
        }
    }
    static func snapshot() async throws -> [TerminalTab] {
        guard let terminal = NSRunningApplication.runningApplications(withBundleIdentifier: "com.apple.Terminal").first else { return [] }
        let result = try await execute("""
        tell application id "com.apple.Terminal"
            set output to {}
            repeat with w in windows
                set wid to id of w
                set wtitle to name of w
                set wm to miniaturized of w
                set wb to bounds of w
                repeat with t in tabs of w
                    set tt to custom title of t
                    if tt is "" then set tt to wtitle
                    set end of output to {tty of t, wid, tt, wm, wb, selected of t}
                end repeat
            end repeat
            return output
        end tell
        """)
        guard result.numberOfItems > 0 else { return [] }
        return (1...result.numberOfItems).compactMap { index in
            guard let item = result.atIndex(index), item.numberOfItems == 6,
                  let tty = item.atIndex(1)?.stringValue, tty.hasPrefix("/dev/") else { return nil }
            let bounds = (1...4).map { Int(item.atIndex(5)?.atIndex($0)?.int32Value ?? 0) }
            return TerminalTab(tty: tty, windowID: Int(item.atIndex(2)?.int32Value ?? 0),
                               title: item.atIndex(3)?.stringValue ?? "Terminal",
                               minimized: item.atIndex(4)?.booleanValue ?? false, bounds: bounds,
                               selected: item.atIndex(6)?.booleanValue ?? false,
                               terminalPID: Int(terminal.processIdentifier))
        }
    }
    @discardableResult
    static func target(_ tab: TerminalTab, commands: String) async throws -> NSAppleEventDescriptor {
        guard NSRunningApplication.runningApplications(withBundleIdentifier: "com.apple.Terminal")
            .contains(where: { Int($0.processIdentifier) == tab.terminalPID }) else {
            throw PilotError(message: "原本的 Terminal 已結束，請重新整理視窗。")
        }
        // Keep an ID-based reference throughout the operation. Restoring/raising a
        // window can reorder `windows`; a loop's positional reference can then
        // point at a different window before the remaining commands execute.
        return try await execute("""
        tell application id "com.apple.Terminal"
            if not (exists window id \(tab.windowID)) then
                error "原本的視窗或分頁已關閉／移動，請重新整理。"
            end if
            set w to a reference to window id \(tab.windowID)
            if not (exists (first tab of w whose tty is \(quote(tab.tty)))) then
                error "原本的視窗或分頁已關閉／移動，請重新整理。"
            end if
            set t to a reference to (first tab of w whose tty is \(quote(tab.tty)))
            \(commands)
            return true
        end tell
        """)
    }
    static func focus(_ tab: TerminalTab) async throws {
        try await target(tab, commands: """
        set selected of t to true
        set miniaturized of w to false
        set frontmost of w to true
        activate
        """)
    }
    static func history(_ tab: TerminalTab) async throws -> String {
        let result = try await target(tab, commands: "return {history of t, contents of t}")
        let history = result.atIndex(1)?.stringValue ?? ""
        let visible = result.atIndex(2)?.stringValue ?? ""
        let current = visible.trimmingCharacters(in: .whitespacesAndNewlines)
        let text = current.isEmpty || history.contains(current) ? history : history + "\n\n── 目前畫面 ──\n" + visible
        return String(text.suffix(60_000))
    }

    static func hasForegroundAgent(_ listing: String, ancestor: Int, tool: String,
                                   executable: String? = nil) -> Bool {
        guard ["codex", "claude"].contains(tool) else { return false }
        var parents: [Int: Int] = [:]
        var candidates: [Int] = []
        for line in listing.split(separator: "\n") {
            let parts = line.split(maxSplits: 4, whereSeparator: { $0.isWhitespace })
            guard parts.count == 5, let pid = Int(parts[0]), let parent = Int(parts[1]),
                  let group = Int(parts[2]), let foreground = Int(parts[3]) else { continue }
            parents[pid] = parent
            let command = String(parts[4])
            let matches = URL(fileURLWithPath: command).lastPathComponent == tool
                || (executable != nil && command == executable)
            if group > 0, group == foreground, matches {
                candidates.append(pid)
            }
        }
        return candidates.contains { candidate in
            var current = candidate, visited: Set<Int> = []
            while visited.insert(current).inserted {
                if current == ancestor { return true }
                guard let parent = parents[current], parent > 1 else { return false }
                current = parent
            }
            return false
        }
    }

    static func foregroundAgent(_ tab: TerminalTab, agentPID: Int, tool: String) async throws -> Bool {
        // Native Claude installs use a version-number filename behind the launcher symlink.
        let executable = RuntimeSupport.executable(tool).map { URL(fileURLWithPath: $0).resolvingSymlinksInPath().path }
        // Inspect executable names, never command arguments (which can contain secrets).
        return try await Task.detached(priority: .userInitiated) {
            let process = Process(), output = Pipe()
            process.executableURL = URL(fileURLWithPath: "/bin/ps")
            process.arguments = ["-t", String(tab.tty.dropFirst(5)), "-o", "pid=,ppid=,pgid=,tpgid=,comm="]
            process.standardOutput = output; process.standardError = FileHandle.nullDevice
            try process.run()
            let data = output.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            return process.terminationStatus == 0 && hasForegroundAgent(String(decoding: data, as: UTF8.self), ancestor: agentPID, tool: tool, executable: executable)
        }.value
    }
    static func stopAgentForResume(_ tab: TerminalTab, agentPID: Int) async throws {
        try await target(tab, commands: "if not busy of t then error \"原 Codex 已離開。\"")
        guard agentPID > 1, try await foregroundAgent(tab, agentPID: agentPID, tool: "codex") else {
            throw PilotError(message: "原 Codex 已離開或前景工作已更換，未停止任何程序。")
        }
        guard kill(pid_t(agentPID), SIGTERM) == 0 else {
            throw PilotError(message: "無法停止原 Codex，未開啟第二份對話。")
        }
        for _ in 0..<80 {
            if kill(pid_t(agentPID), 0) != 0 && errno == ESRCH {
                // The old process must be fully gone before another writer resumes its conversation.
                if !(try await target(tab, commands: "return busy of t")).booleanValue { return }
            }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        throw PilotError(message: "原 Codex 尚未完全結束，未開啟第二份對話。請稍後從專案續接。")
    }
    static func sendAgent(_ tab: TerminalTab, agentPID: Int, tool: String, text: String) async throws {
        let valid = try await foregroundAgent(tab, agentPID: agentPID, tool: tool)
        guard valid else { throw PilotError(message: "這個視窗目前不是原本的 agent 在接收輸入，未送出。請回電腦確認。") }
        // Bracketed paste keeps multiline text together.
        // Terminal's do script appends Return; the window need not be activated.
        let payload = "\u{1b}[200~" + text + "\u{1b}[201~"
        try await target(tab, commands: """
        if not busy of t then error "Agent 已離開，未送出。"
        do script \(quote(payload)) in t
        """)
    }
    static func minimize(_ tab: TerminalTab) async throws {
        try await target(tab, commands: "set miniaturized of w to true")
    }
    private static func executablePath(_ pid: Int32) -> String? {
        // PROC_PIDPATHINFO_MAXSIZE is 4 * MAXPATHLEN; the compound C macro
        // is not imported into Swift by the macOS 13 SDK.
        var path = [CChar](repeating: 0, count: 4 * Int(MAXPATHLEN))
        guard proc_pidpath(pid, &path, UInt32(path.count)) > 0 else { return nil }
        return String(cString: path)
    }
    static func closeProcesses(_ listing: String, uid: Int,
                               executable: (Int32) -> String? = executablePath) throws -> [(Int32, Int32)] {
        try listing.split(separator: "\n").compactMap { line in
            let values = line.split(whereSeparator: { $0.isWhitespace }).compactMap { Int32($0) }
            guard values.count == 3, values[0] > 1, values[1] > 1,
                  values[0] != getpid() else {
                throw PilotError(message: "視窗內有無法安全停止的程序，請在電腦關閉。")
            }
            // Terminal's root-owned /usr/bin/login waits for its child shell to
            // exit and performs PAM cleanup. Never signal that supervisor.
            // Every other foreign-UID process still blocks a mobile close.
            if values[2] == 0 && executable(values[0]) == "/usr/bin/login" { return nil }
            guard Int(values[2]) == uid else {
                throw PilotError(message: "視窗內有其他使用者的程序（UID \(values[2])），請在電腦關閉。")
            }
            return (values[0], values[1])
        }
    }
    private static func processesOnTTY(_ tty: String) async throws -> [(Int32, Int32)] {
        try await Task.detached(priority: .userInitiated) {
            let process = Process(), output = Pipe()
            process.executableURL = URL(fileURLWithPath: "/bin/ps")
            process.arguments = ["-t", String(tty.dropFirst(5)), "-o", "pid=,pgid=,uid="]
            process.standardOutput = output; process.standardError = FileHandle.nullDevice
            try process.run()
            let data = output.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            guard [0, 1].contains(process.terminationStatus) else { throw PilotError(message: "無法確認 Terminal 程序，未關閉視窗。") }
            return try closeProcesses(String(decoding: data, as: UTF8.self), uid: Int(getuid()))
        }.value
    }
    static func closeWindow(_ expected: [TerminalTab]) async throws {
        guard let first = expected.first else { throw PilotError(message: "沒有可關閉的視窗。") }
        let bindings = Set(expected.map(\.id))
        func windowTabs() async throws -> [TerminalTab] {
            try await snapshot().filter { $0.terminalPID == first.terminalPID && $0.windowID == first.windowID }
        }
        guard Set(try await windowTabs().map(\.id)) == bindings else {
            throw PilotError(message: "視窗分頁已改變，未關閉。請重新確認。")
        }
        // Closing a terminal hangs up its jobs. Signal only this confirmed window's
        // TTY processes, never command-name matches or another terminal's group.
        var jobs: [(Int32, Int32)] = []
        for tab in expected { jobs += try await processesOnTTY(tab.tty) }
        guard Set(try await windowTabs().map(\.id)) == bindings else { throw PilotError(message: "視窗已改變，未停止程序。") }
        for (pid, group) in jobs where getpgid(pid) == group {
            if kill(pid, SIGHUP) != 0 && errno != ESRCH { throw PilotError(message: "程序未能結束，請在電腦確認。") }
        }
        for _ in 0..<50 {
            let remaining = try await windowTabs()
            guard Set(remaining.map(\.id)).isSubset(of: bindings) else { throw PilotError(message: "視窗新增了分頁，未關閉新增的工作。") }
            if remaining.isEmpty { return }
            var active = false
            for tab in remaining { if !(try await processesOnTTY(tab.tty)).isEmpty { active = true } }
            if !active, let targetTab = remaining.first {
                try await target(targetTab, commands: """
                if (count tabs of w) is not \(remaining.count) then error "視窗分頁已改變，未關閉。"
                repeat with candidate in tabs of w
                    if tty of candidate is not in {\(remaining.map { quote($0.tty) }.joined(separator: ", "))} then error "視窗分頁已改變，未關閉。"
                end repeat
                close w saving no
                """)
                if try await windowTabs().isEmpty { return }
            }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        throw PilotError(message: "視窗內的程序尚未結束，請在電腦確認；未強制終止。")
    }
    static func place(_ tab: TerminalTab, bounds: [Int], minimized: Bool = false) async throws {
        guard bounds.count == 4 else { return }
        let rectangle = "{" + bounds.map(String.init).joined(separator: ", ") + "}"
        try await target(tab, commands: """
        set miniaturized of w to false
        set bounds of w to \(rectangle)
        set miniaturized of w to \(minimized ? "true" : "false")
        """)
    }
    static func launch(command: String, title: String = "") async throws -> TerminalTab {
        let result = try await execute("""
        tell application id "com.apple.Terminal"
            set newTab to do script \(quote(command))
            \(title.isEmpty ? "" : "set custom title of newTab to \(quote(title))")
            activate
            set newTTY to tty of newTab
            repeat with w in windows
                repeat with t in tabs of w
                    if tty of t is newTTY then
                        return {newTTY, id of w, name of w, miniaturized of w, bounds of w, selected of t}
                    end if
                end repeat
            end repeat
            error "新視窗已關閉，請重新開啟。"
        end tell
        """)
        guard result.numberOfItems == 6, let tty = result.atIndex(1)?.stringValue,
              let terminal = NSRunningApplication.runningApplications(withBundleIdentifier: "com.apple.Terminal").first else {
            throw PilotError(message: "無法確認新開啟的 Terminal 視窗。")
        }
        return TerminalTab(tty: tty, windowID: Int(result.atIndex(2)?.int32Value ?? 0),
                           title: title.isEmpty ? result.atIndex(3)?.stringValue ?? "Terminal" : title,
                           minimized: result.atIndex(4)?.booleanValue ?? false,
                           bounds: (1...4).map { Int(result.atIndex(5)?.atIndex($0)?.int32Value ?? 0) },
                           selected: result.atIndex(6)?.booleanValue ?? false,
                           terminalPID: Int(terminal.processIdentifier))
    }
}
