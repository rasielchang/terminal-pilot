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
    var automationCode: Int? = nil
    var errorDescription: String? { message }
}

struct PartialTerminalSnapshot: LocalizedError {
    let tabs: [TerminalTab]
    let unidentifiedWindows: Int
    var invalidKinds: [String] = []
    var errorDescription: String? {
        "有 \(unidentifiedWindows) 個 Terminal 項目未提供有效視窗識別；已隔離此項目，其他工作依各自讀取結果顯示。"
    }
}

struct TerminalCloseError: LocalizedError {
    let stage: String
    let started: Bool
    let cause: Error
    var errorDescription: String? { "關窗失敗（\(stage)）：\(cause.localizedDescription)" }
    var diagnostic: [String: Any] {
        ["stage": stage, "type": "TerminalCloseError", "message": cause.localizedDescription,
         "delivery": started ? "unknown" : "not_started", "retryable": false,
         "code": (cause as? PilotError)?.automationCode ?? 0]
    }
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
                    continuation.resume(throwing: PilotError(message: text, automationCode: code))
                } else { continuation.resume(returning: result) }
            }
        }
    }
    static let snapshotScript = """
        tell application id "com.apple.Terminal"
            -- Resolve IDs in one Apple event. A repeat over `windows` keeps
            -- positional specifiers such as item 5 of every window alive;
            -- closing/reordering a window can invalidate the whole enumeration.
            set windowIDs to get id of every window
            set output to {}
            set unidentifiedWindows to 0
            set readableIDs to {}
            set invalidKinds to {}
            repeat with windowID in windowIDs
                set wid to contents of windowID
                -- Terminal can include an auxiliary/stale object with a
                -- missing ID. It cannot be addressed as `window id` safely.
                if (class of wid) is integer then
                    if wid > 0 then
                        set end of readableIDs to wid
                    else
                        set unidentifiedWindows to unidentifiedWindows + 1
                        set end of invalidKinds to "nonpositive"
                    end if
                else
                    set unidentifiedWindows to unidentifiedWindows + 1
                    if wid is missing value then
                        set end of invalidKinds to "missing_value"
                    else
                        set end of invalidKinds to "noninteger"
                    end if
                end if
            end repeat
            repeat with windowID in readableIDs
                set wid to contents of windowID
                set w to a reference to window id wid
                set wtitle to name of w
                set wm to miniaturized of w
                set wb to bounds of w
                repeat with t in tabs of w
                    set tt to custom title of t
                    if tt is "" then set tt to wtitle
                    set end of output to {tty of t, wid, tt, wm, wb, selected of t}
                end repeat
            end repeat
            set finalIDs to get id of every window
            if (count windowIDs) is not (count finalIDs) then error "Terminal windows changed during refresh" number -1728
            repeat with windowID in readableIDs
                if (contents of windowID) is not in finalIDs then error "Terminal windows changed during refresh" number -1728
            end repeat
            return {output, unidentifiedWindows, invalidKinds}
        end tell
        """
    static func snapshot() async throws -> [TerminalTab] {
        guard let terminal = NSRunningApplication.runningApplications(withBundleIdentifier: "com.apple.Terminal").first else { return [] }
        return try await stableSnapshot {
            let result = try await execute(snapshotScript)
            guard !terminal.isTerminated else {
                throw PilotError(message: "Terminal 已重新啟動，正在重新讀取視窗。", automationCode: -1728)
            }
            return try decodeWindowSnapshot(result, terminalPID: Int(terminal.processIdentifier))
        }
    }
    // Read this exact window, independently of unrelated invalid window objects.
    // A successful `exists window id` is the only absence proof used here.
    static func windowSnapshot(_ tab: TerminalTab) async throws -> [TerminalTab] {
        guard NSRunningApplication.runningApplications(withBundleIdentifier: "com.apple.Terminal")
            .contains(where: { Int($0.processIdentifier) == tab.terminalPID }) else {
            throw PilotError(message: "原 Terminal 程序已改變，未操作視窗。")
        }
        let result = try await execute(windowSnapshotScript(tab))
        return try decodeSnapshot(result, terminalPID: tab.terminalPID)
    }
    // Read-only reconciliation, including an original Terminal process that
    // has exited. Unlike a close operation, this never launches or signals it.
    static func probeWindow(_ tab: TerminalTab) async throws -> [TerminalTab] {
        guard NSRunningApplication.runningApplications(withBundleIdentifier: "com.apple.Terminal")
            .contains(where: { Int($0.processIdentifier) == tab.terminalPID }) else { return [] }
        guard let birth = processBirth(tab.terminalPID) else {
            throw PilotError(message: "無法確認原 Terminal 程序。")
        }
        let result = try await windowSnapshot(tab)
        guard processBirth(tab.terminalPID) == birth else {
            throw PilotError(message: "Terminal 在核對期間重新啟動。", automationCode: -1728)
        }
        return result
    }
    static func windowSnapshotScript(_ tab: TerminalTab) -> String {
        """
        tell application id "com.apple.Terminal"
            if not (exists window id \(tab.windowID)) then return {}
            set w to a reference to window id \(tab.windowID)
            set output to {}
            set beforeTTYs to get tty of every tab of w
            repeat with t in tabs of w
                set end of output to {tty of t, \(tab.windowID), name of w, miniaturized of w, bounds of w, selected of t}
            end repeat
            if beforeTTYs is not (get tty of every tab of w) then error "Target tabs changed during read" number -1728
            return output
        end tell
        """
    }
    static func decodeWindowSnapshot(_ result: NSAppleEventDescriptor, terminalPID: Int) throws -> [TerminalTab] {
        guard result.descriptorType == typeAEList, [2, 3].contains(result.numberOfItems),
              let rows = result.atIndex(1), let incomplete = result.atIndex(2),
              incomplete.descriptorType == typeSInt32, incomplete.int32Value >= 0 else {
            throw PilotError(message: "Terminal 視窗資料不完整，正在重試。", automationCode: -1728)
        }
        let tabs = try decodeSnapshot(rows, terminalPID: terminalPID)
        if incomplete.int32Value > 0 {
            let kinds = result.atIndex(3)
            let values = (kinds?.numberOfItems ?? 0) > 0 ? (1...min(kinds!.numberOfItems, 32)).compactMap { kinds?.atIndex($0)?.stringValue }.filter { ["missing_value", "noninteger", "nonpositive"].contains($0) } : []
            throw PartialTerminalSnapshot(tabs: tabs, unidentifiedWindows: Int(incomplete.int32Value), invalidKinds: values)
        }
        return tabs
    }
    static func decodeSnapshot(_ result: NSAppleEventDescriptor, terminalPID: Int) throws -> [TerminalTab] {
        guard result.descriptorType == typeAEList else {
            throw PilotError(message: "Terminal 視窗資料不完整，正在重試。", automationCode: -1728)
        }
        guard result.numberOfItems > 0 else { return [] }
        return try (1...result.numberOfItems).map { index in
            guard let item = result.atIndex(index), item.numberOfItems == 6,
                  let tty = item.atIndex(1)?.stringValue, tty.hasPrefix("/dev/"),
                  item.atIndex(5)?.numberOfItems == 4, (item.atIndex(2)?.int32Value ?? 0) > 0 else {
                throw PilotError(message: "Terminal 視窗資料不完整，正在重試。", automationCode: -1728)
            }
            let bounds = (1...4).map { Int(item.atIndex(5)?.atIndex($0)?.int32Value ?? 0) }
            return TerminalTab(tty: tty, windowID: Int(item.atIndex(2)?.int32Value ?? 0),
                               title: item.atIndex(3)?.stringValue ?? "Terminal",
                               minimized: item.atIndex(4)?.booleanValue ?? false, bounds: bounds,
                               selected: item.atIndex(6)?.booleanValue ?? false,
                               terminalPID: terminalPID)
        }
    }
    static func stableSnapshot(_ read: () async throws -> [TerminalTab]) async throws -> [TerminalTab] {
        do { return try await read() }
        catch let error as PilotError where error.automationCode == -1728 {
            // Retry one complete read, never publish a partial list as a
            // reliable closure observation. Permission failures are not retried.
            return try await read()
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
        let address = "(first tab of window id \(tab.windowID) whose tty is \(quote(tab.tty)))"
        let result = try await target(tab, commands: "return {history of \(address), contents of \(address)}")
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
    static func processBirth(_ pid: Int) -> String? {
        var info = proc_bsdinfo()
        guard proc_pidinfo(Int32(pid), PROC_PIDTBSDINFO, 0, &info, Int32(MemoryLayout<proc_bsdinfo>.size)) == MemoryLayout<proc_bsdinfo>.size else { return nil }
        return "\(info.pbi_start_tvsec).\(info.pbi_start_tvusec)"
    }
    static func agentIdentity(_ pid: Int, _ tty: String, _ created: Double) -> String? {
        var info = proc_bsdinfo(), device = stat()
        guard pid > 1, tty.hasPrefix("/dev/"), lstat(tty, &device) == 0,
              (device.st_mode & S_IFMT) == S_IFCHR,
              proc_pidinfo(Int32(pid), PROC_PIDTBSDINFO, 0, &info, Int32(MemoryLayout<proc_bsdinfo>.size)) == MemoryLayout<proc_bsdinfo>.size,
              info.e_tdev == UInt32(bitPattern: device.st_rdev),
              Double(info.pbi_start_tvsec) + Double(info.pbi_start_tvusec) / 1_000_000 <= created + 0.001 else { return nil }
        return "\(pid):\(info.pbi_start_tvsec).\(info.pbi_start_tvusec):\(info.e_tdev)"
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
        try await closeWindow(expected, readWindow: windowSnapshot, processes: processesOnTTY,
            hangup: { pid, group in
                if getpgid(pid) == group, kill(pid, SIGHUP) != 0 && errno != ESRCH {
                    throw PilotError(message: "程序未能結束，請在電腦確認。")
                }
            }, close: { tab, remaining in
                try await target(tab, commands: """
                if (count tabs of w) is not \(remaining.count) then error "視窗分頁已改變，未關閉。"
                repeat with candidate in tabs of w
                    if tty of candidate is not in {\(remaining.map { quote($0.tty) }.joined(separator: ", "))} then error "視窗分頁已改變，未關閉。"
                end repeat
                close w saving no
                """)
            })
    }
    static func closeWindow(_ expected: [TerminalTab],
                            readWindow: (TerminalTab) async throws -> [TerminalTab],
                            processes: (String) async throws -> [(Int32, Int32)],
                            hangup: (Int32, Int32) throws -> Void,
                            close: (TerminalTab, [TerminalTab]) async throws -> Void) async throws {
        guard let first = expected.first else { throw PilotError(message: "沒有可關閉的視窗。") }
        let bindings = Set(expected.map(\.id))
        func windowTabs() async throws -> [TerminalTab] {
            try await readWindow(first)
        }
        var stage = "target_snapshot", started = false
        do {
        guard Set(try await windowTabs().map(\.id)) == bindings else {
            throw PilotError(message: "視窗分頁已改變，未關閉。請重新確認。")
        }
        // Closing a terminal hangs up its jobs. Signal only this confirmed window's
        // TTY processes, never command-name matches or another terminal's group.
        var jobs: [(Int32, Int32)] = []
        stage = "process_inventory"
        for tab in expected { jobs += try await processes(tab.tty) }
        stage = "target_recheck"
        guard Set(try await windowTabs().map(\.id)) == bindings else { throw PilotError(message: "視窗已改變，未停止程序。") }
        stage = "process_hangup"; started = true
        for (pid, group) in jobs { try hangup(pid, group) }
        for _ in 0..<50 {
            stage = "verify_window"
            let remaining = try await windowTabs()
            guard Set(remaining.map(\.id)).isSubset(of: bindings) else { throw PilotError(message: "視窗新增了分頁，未關閉新增的工作。") }
            if remaining.isEmpty { return }
            var active = false
            stage = "wait_process_exit"
            for tab in remaining { if !(try await processes(tab.tty)).isEmpty { active = true } }
            if !active, let targetTab = remaining.first {
                stage = "window_close"
                try await close(targetTab, remaining)
                stage = "verify_window"
                if try await windowTabs().isEmpty { return }
            }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        throw PilotError(message: "視窗內的程序尚未結束，請在電腦確認；未強制終止。")
        } catch { throw TerminalCloseError(stage: stage, started: started, cause: error) }
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
