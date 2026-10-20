import AppKit
import SwiftUI
import Darwin

struct AgentEvent: Codable, Identifiable {
    var id: String
    var tty: String
    var tool: String
    var cwd: String
    var label: String
    var pid: Int
    var phase: String
    var createdAt: Double
    var updatedAt: Double
    var noticeID: String
    var noticeAt: Double
    var noticePhase: String?
    var lifecycle: String? = nil
    var codexThreadID: String? = nil
    var codexHome: String? = nil
    var codexSocket: String? = nil
    var codexTranscript: String? = nil
    var claudeSessionID: String? = nil
    var claudeHome: String? = nil
    var claudeTranscript: String? = nil
    var activeTurn: String? = nil
    var activeTurnStartedAt: Double? = nil
    var lastTurn: String? = nil
    var noticeReason: String? = nil
    var approvalReviewer: String? = nil
    var waitingTool: String? = nil
    var lifecycleReady: Bool { lifecycle == "ready" || lifecycle == "runtime" }
    var alive: Bool { kill(pid_t(pid), 0) == 0 || errno == EPERM }
    var toolName: String { tool == "claude" ? "Claude Code" : tool == "codex" ? "Codex CLI" : "通知測試" }
}

struct Row: Identifiable {
    var tab: TerminalTab?
    var event: AgentEvent?
    var title: String
    var unread: Bool
    var activityAt: Double = 0
    var id: String { tab?.id ?? "offline:\(event?.id ?? "")" }
    var webID: String { event?.id ?? "terminal:\(id)" }
    var sortAt: Double {
        if unread, let event { return event.noticeAt }
        return max(activityAt, event?.updatedAt ?? 0)
    }
    static func ordered(_ lhs: Row, _ rhs: Row) -> Bool {
        if lhs.unread != rhs.unread { return lhs.unread }
        if lhs.sortAt != rhs.sortAt { return lhs.sortAt > rhs.sortAt }
        return lhs.id < rhs.id
    }
    var phase: String {
        guard let event else { return "unlinked" }
        if unread { return event.noticePhase ?? event.phase }
        if !event.alive && event.phase != "complete" { return "ended" }
        return event.phase
    }
    var status: String {
        switch phase {
        case "running": return "執行中"
        case "complete": return unread ? "回覆完成 · 待查看" : "最近回報：回覆完成"
        case "waiting": return "等你處理"
        case "error": return "執行發生錯誤"
        case "ended": return "AI 已離開"
        case "idle": return "已停止 · 等待新指令"
        case "connected":
            if event?.codexSocket?.isEmpty == false { return "手機核准已連接 · 等待輸入" }
            return event?.tool == "codex" && event?.lifecycleReady == false ? "完成通知已連接" : "等待輸入"
        default: return "尚未連接 AI 通知"
        }
    }
    var tint: Color {
        if unread { return phase == "waiting" ? .orange : .red }
        return phase == "running" ? .mint : .secondary
    }
}

enum AgentStartMode: String, Codable, CaseIterable {
    case new, picker, latest, specific
    var title: String {
        switch self {
        case .new: return "建立新對話"
        case .picker: return "選擇舊對話"
        case .latest: return "續接此專案最近對話"
        case .specific: return "續接指定對話"
        }
    }
}

struct AgentSession: Codable, Equatable {
    var mode: AgentStartMode = .new
    var sessionID = ""
    var allProjects = false
    var isValid: Bool {
        guard mode == .specific else { return true }
        let value = sessionID.trimmingCharacters(in: .whitespacesAndNewlines)
        return !value.isEmpty && !value.hasPrefix("-") && value.rangeOfCharacter(from: .controlCharacters) == nil
    }
    func arguments(for tool: String) -> [String] {
        if tool == "claude" {
            switch mode {
            case .new: return []
            case .picker: return ["--resume"]
            case .latest: return ["--continue"]
            case .specific: return ["--resume", sessionID.trimmingCharacters(in: .whitespacesAndNewlines)]
            }
        }
        guard tool == "codex" else { return [] }
        switch mode {
        case .new: return []
        case .picker: return allProjects ? ["resume", "--all"] : ["resume"]
        case .latest: return ["resume", "--last"]
        case .specific: return ["resume", sessionID.trimmingCharacters(in: .whitespacesAndNewlines)]
        }
    }
}

struct SavedProject: Codable, Identifiable, Equatable {
    var id = UUID().uuidString
    var name: String
    var directory: String
    var tool: String
    var pinned = false
    var lastOpenedAt: Double
    var codexSession: AgentSession? = nil
    var claudeSession: AgentSession? = nil
    var isAgent: Bool { tool == "codex" || tool == "claude" }
    // Preserve the existing Codex storage key so saved projects migrate intact.
    var session: AgentSession? {
        get { tool == "codex" ? codexSession : tool == "claude" ? claudeSession : nil }
        set {
            codexSession = tool == "codex" ? newValue : nil
            claudeSession = tool == "claude" ? newValue : nil
        }
    }
    var toolName: String { tool == "claude" ? "Claude Code" : tool == "codex" ? "Codex CLI" : "Terminal" }
}

struct Preferences: Codable, Equatable {
    var acknowledged: [String: String] = [:]
    var bindings: [String: String] = [:]
    var aliases: [String: String] = [:]
    var order: [String] = []
    var dismissed: Set<String> = []
    var lastDirectory: String = NSHomeDirectory()
    var pinned: Bool = true
    var tabActivity: [String: Double] = [:]
    var projects: [SavedProject] = []
    var projectBindings: [String: String] = [:]

    init() {}
    enum CodingKeys: String, CodingKey {
        case acknowledged, bindings, aliases, order, dismissed, lastDirectory, pinned
        case tabActivity, projects, projectBindings
    }
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        acknowledged = try values.decodeIfPresent([String: String].self, forKey: .acknowledged) ?? [:]
        bindings = try values.decodeIfPresent([String: String].self, forKey: .bindings) ?? [:]
        aliases = try values.decodeIfPresent([String: String].self, forKey: .aliases) ?? [:]
        order = try values.decodeIfPresent([String].self, forKey: .order) ?? []
        dismissed = try values.decodeIfPresent(Set<String>.self, forKey: .dismissed) ?? []
        lastDirectory = try values.decodeIfPresent(String.self, forKey: .lastDirectory) ?? NSHomeDirectory()
        pinned = try values.decodeIfPresent(Bool.self, forKey: .pinned) ?? true
        tabActivity = try values.decodeIfPresent([String: Double].self, forKey: .tabActivity) ?? [:]
        projects = try values.decodeIfPresent([SavedProject].self, forKey: .projects) ?? []
        projectBindings = try values.decodeIfPresent([String: String].self, forKey: .projectBindings) ?? [:]
    }
}

struct RecallClick {
    var count: Int
    var timestamp: TimeInterval
    var windowNumber: Int
    var location: NSPoint
    var interval: TimeInterval

    @MainActor static var current: RecallClick? {
        guard let event = NSApp?.currentEvent,
              event.type == .leftMouseUp || event.type == .leftMouseDown else { return nil }
        return RecallClick(count: event.clickCount, timestamp: event.timestamp,
                           windowNumber: event.windowNumber, location: event.locationInWindow,
                           interval: NSEvent.doubleClickInterval)
    }

    func continues(_ previous: RecallClick) -> Bool {
        count > 1 && count >= previous.count && windowNumber == previous.windowNumber
            && timestamp >= previous.timestamp
            && timestamp - previous.timestamp <= interval * Double(max(1, count - previous.count))
            && abs(location.x - previous.location.x) <= 4 && abs(location.y - previous.location.y) <= 4
    }
}

@MainActor final class PilotModel: ObservableObject {
    @Published var rows: [Row] = []
    @Published var error: String?
    @Published var refreshing = false
    @Published var acting = false
    @Published var selection: Set<String> = []
    @Published var unreadOnly = false
    @Published var query = ""
    @Published var showNew = false
    @Published var showHelp = false
    @Published var showSetup = false
    @Published var showWeb = false
    @Published var webStarting = false
    @Published var webLAN = false
    @Published var webHTTPS = false
    @Published var webURLs: [String] = []
    @Published var webPairingCode = ""
    @Published var webError: String?
    private var webToken = ""
    private var webBridge: LocalWebBridge?
    @Published var showingProjects = false
    @Published var projectToSave: Row?
    @Published var projectToConfigure: SavedProject?
    @Published var projectToOpen: SavedProject?
    @Published var layoutAvailable = false
    @Published var pinned = true
    let store: URL
    @Published var preferences = Preferences()
    var onCount: ((Int) -> Void)?
    var onPin: ((Bool) -> Void)?
    private var tabs: [TerminalTab] = []
    private var events: [AgentEvent] = []
    private var previousLayout: [TerminalTab] = []
    private var timer: Timer?
    private var pollingPaused = false
    private var lastRecallClick: RecallClick?
    private let snapshot: () async throws -> [TerminalTab]
    private let launch: (String, String) async throws -> TerminalTab
    private let validateTool: (String) throws -> Void
    private let bringToFront: (TerminalTab) async throws -> Void
    private let readTerminal: (TerminalTab) async throws -> String
    private let sendToAgent: (TerminalTab, Int, String, String) async throws -> Void
    private let sendToTerminal: (TerminalTab, String) async throws -> Void
    private var terminalInputJournal: TerminalInputJournal!
    private let stopForResume: (TerminalTab, Int) async throws -> Void
    private let closeTerminal: ([TerminalTab]) async throws -> Void
    private let inspectClaudeTrust: (TerminalTab, Int, String) async throws -> ClaudeTrustPrompt?
    private let answerClaudeTrust: (TerminalTab, Int, String, String) async throws -> Void
    private let inspectStartup: (TerminalTab, Int) async throws -> StartupPrompt?
    private let answerStartup: (TerminalTab, Int, String, String) async throws -> Void
    private var trustReceipts: [String: ClaudeTrustReceipt] = [:]
    private var trustJournalError: String?
    private var webSending = false

    init(validateTool: @escaping (String) throws -> Void = RuntimeSupport.requireTool,
         storeURL: URL? = nil,
         snapshot: @escaping () async throws -> [TerminalTab] = TerminalBridge.snapshot,
         launch: @escaping (String, String) async throws -> TerminalTab = TerminalBridge.launch,
         bringToFront: @escaping (TerminalTab) async throws -> Void = TerminalBridge.focus,
         readTerminal: @escaping (TerminalTab) async throws -> String = TerminalBridge.history,
         sendToAgent: @escaping (TerminalTab, Int, String, String) async throws -> Void = TerminalBridge.sendAgent,
         sendToTerminal: @escaping (TerminalTab, String) async throws -> Void = TerminalBridge.sendTerminal,
         stopForResume: @escaping (TerminalTab, Int) async throws -> Void = TerminalBridge.stopAgentForResume,
         closeTerminal: @escaping ([TerminalTab]) async throws -> Void = TerminalBridge.closeWindow,
         inspectClaudeTrust: @escaping (TerminalTab, Int, String) async throws -> ClaudeTrustPrompt? = TerminalBridge.claudeTrust,
         answerClaudeTrust: @escaping (TerminalTab, Int, String, String) async throws -> Void = TerminalBridge.answerClaudeTrust,
         inspectStartup: @escaping (TerminalTab, Int) async throws -> StartupPrompt? = TerminalBridge.startupPrompt,
         answerStartup: @escaping (TerminalTab, Int, String, String) async throws -> Void = TerminalBridge.answerStartup) {
        self.snapshot = snapshot
        self.launch = launch
        self.validateTool = validateTool
        self.bringToFront = bringToFront
        self.readTerminal = readTerminal
        self.sendToAgent = sendToAgent
        self.sendToTerminal = sendToTerminal
        self.stopForResume = stopForResume
        self.closeTerminal = closeTerminal
        self.inspectClaudeTrust = inspectClaudeTrust
        self.answerClaudeTrust = answerClaudeTrust
        self.inspectStartup = inspectStartup
        self.answerStartup = answerStartup
        let custom = ProcessInfo.processInfo.environment["TERMINAL_PILOT_STORE"]
        store = storeURL ?? custom.map { URL(fileURLWithPath: $0) } ?? FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/Terminal Pilot")
        do {
            try FileManager.default.createDirectory(at: store, withIntermediateDirectories: true,
                                                   attributes: [.posixPermissions: 0o700])
            let file = store.appendingPathComponent("preferences.json")
            if FileManager.default.fileExists(atPath: file.path) {
                preferences = try JSONDecoder().decode(Preferences.self, from: Data(contentsOf: file))
            }
        } catch { self.error = "無法讀取管理器設定：\(error.localizedDescription)" }
        let journal = store.appendingPathComponent("claude-trust-receipts.json")
        if FileManager.default.fileExists(atPath: journal.path) {
            do { trustReceipts = try JSONDecoder().decode([String: ClaudeTrustReceipt].self, from: Data(contentsOf: journal)) }
            catch { trustJournalError = "無法讀取先前的資料夾確認紀錄，請在電腦確認。" }
        }
        pinned = preferences.pinned
        terminalInputJournal = TerminalInputJournal(store: store)
    }
    var unreadCount: Int { rows.filter(\.unread).count }
    var windowCount: Int { Set(tabs.map(\.windowID)).count }
    var selectedCount: Int { Set(tabs.filter { selection.contains($0.id) }.map(\.windowID)).count }
    var filteredRows: [Row] {
        rows.filter { (!unreadOnly || $0.unread) && (query.isEmpty || ($0.title + " " + ($0.event?.toolName ?? "Terminal") + " " + directory(for: $0)).localizedCaseInsensitiveContains(query)) }
    }
    var filteredProjects: [SavedProject] {
        preferences.projects.filter {
            query.isEmpty || ($0.name + " " + $0.directory + " " + $0.toolName).localizedCaseInsensitiveContains(query)
        }.sorted {
            if $0.pinned != $1.pinned { return $0.pinned }
            if $0.lastOpenedAt != $1.lastOpenedAt { return $0.lastOpenedAt > $1.lastOpenedAt }
            return $0.id < $1.id
        }
    }
    func project(for row: Row) -> SavedProject? {
        preferences.projects.first { $0.id == preferences.projectBindings[row.id] }
    }
    func directory(for row: Row) -> String {
        row.event?.cwd ?? project(for: row)?.directory ?? ""
    }
    func start() {
        webLAN = UserDefaults.standard.bool(forKey: "webLAN")
        webHTTPS = UserDefaults.standard.bool(forKey: "webHTTPS")
        if UserDefaults.standard.bool(forKey: "webEnabled") { startWeb() }
        Task { await refresh(force: true) }
        timer = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: true) { [weak self] _ in
            guard let model = self else { return }
            Task { @MainActor in await model.refresh() }
        }
    }
    func startWeb(resetAuth: Bool = false) {
        stopWeb(remember: false)
        UserDefaults.standard.set(true, forKey: "webEnabled")
        UserDefaults.standard.set(webLAN, forKey: "webLAN")
        UserDefaults.standard.set(webHTTPS, forKey: "webHTTPS")
        webStarting = true; webError = nil
        let bridge = LocalWebBridge()
        webBridge = bridge
        do {
            try bridge.start(lan: webLAN, https: webHTTPS, resetAuth: resetAuth,
                             pairing: { [weak self] code in self?.webPairingCode = code }, ready: { [weak self] info in
                guard let self, let port = info["port"] as? Int, let token = info["token"] as? String else { return }
                self.webToken = token
                self.webPairingCode = info["pairingCode"] as? String ?? ""
                let addresses = info["addresses"] as? [String] ?? ["127.0.0.1"]
                self.webURLs = ["http://127.0.0.1:\(port)"] + addresses.filter { $0 != "127.0.0.1" }.map { "http://\($0):\(port)" }
                if let httpsURL = info["httpsURL"] as? String, !httpsURL.isEmpty { self.webURLs.insert(httpsURL, at: 0) }
                if let message = info["httpsError"] as? String, !message.isEmpty { self.webError = message }
                self.webStarting = false
            }, failed: { [weak self] message in
                self?.stopWeb(remember: false); self?.webError = message
            }, request: { [weak self] method, params in
                guard let self else { throw PilotError(message: "Terminal Pilot 已關閉。") }
                return try await self.webRequest(method, params: params)
            })
        } catch { stopWeb(remember: false); webError = error.localizedDescription }
    }
    func refreshWebPairing() { webBridge?.refreshPairing() }
    func stopWeb(remember: Bool = true) {
        if remember { UserDefaults.standard.set(false, forKey: "webEnabled") }
        webBridge?.stop(); webBridge = nil
        webStarting = false; webURLs = []; webPairingCode = ""; webToken = ""
    }
    func openWeb() {
        guard let address = webURLs.first, let url = URL(string: address + "/#token=" + webToken) else { return }
        NSWorkspace.shared.open(url)
    }
    func showWebSettings() {
        showWeb = true
        if webURLs.isEmpty && !webStarting { startWeb() }
    }
    func webRequest(_ method: String, params: [String: Any]) async throws -> [String: Any] {
        // Refresh before resolving IDs. A browser never supplies an AppleScript target or a PID.
        await refresh()
        readEvents(); rebuild()
        if method == "list" {
            var sessions: [[String: Any]] = []
            for row in rows {
                guard let event = row.event else {
                    sessions.append(["id": row.webID, "binding": row.tab?.id ?? "", "title": row.title,
                            "tool": "terminal", "toolName": "Terminal", "directory": self.directory(for: row),
                            "status": row.status, "phase": row.phase, "unread": false,
                            "noticeID": "", "updatedAt": row.activityAt, "canSend": row.tab != nil,
                            "lifecycleReady": false])
                    continue
                }
                var session: [String: Any] = ["id": event.id, "binding": row.tab?.id ?? "", "title": row.title,
                        "tool": event.tool, "toolName": event.toolName,
                        "agentPID": event.pid, "createdAt": event.createdAt,
                        "alive": event.alive,
                        "directory": event.cwd, "status": row.status, "phase": event.phase,
                        "unread": row.unread, "noticeID": event.noticeID, "updatedAt": event.updatedAt,
                        "noticeReason": event.noticeReason ?? "", "approvalReviewer": event.approvalReviewer ?? "",
                        "waitingTool": event.waitingTool ?? "",
                        "canSend": ["codex", "claude"].contains(event.tool) && row.tab != nil && event.alive && !["waiting", "ended", "error"].contains(event.phase),
                        "codexThreadID": event.codexThreadID ?? "", "activeTurn": event.activeTurn ?? "",
                        "activeTurnStartedAt": event.activeTurnStartedAt ?? 0,
                        "codexHome": event.codexHome ?? "", "codexTranscript": event.codexTranscript ?? "",
                        "codexSocket": event.codexSocket ?? "",
                        "claudeSessionID": event.claudeSessionID ?? "", "claudeHome": event.claudeHome ?? "",
                        "claudeTranscript": event.claudeTranscript ?? "",
                        "lifecycleReady": event.lifecycleReady,
                        "lastTurn": event.lastTurn ?? ""]
                if trustEligible(event), let tab = row.tab,
                   let prompt = try? await inspectClaudeTrust(tab, event.pid, event.cwd) {
                    let id = ClaudeTrustPrompt.identity(event: event, tab: tab)
                    let state = trustJournalError == nil ? trustReceipts[id]?.state ?? "pending" : "uncertain"
                    session["runtime"] = ["state": "approval", "approvals": [prompt.card(id: id, directory: event.cwd, state: state)]]
                    session["phase"] = "waiting"; session["canSend"] = false
                    session["status"] = "等待你確認資料夾"; session["approvalReviewer"] = "user"
                    session["noticeReason"] = "permission"
                }
                if startupEligible(event), let tab = row.tab,
                   let prompt = try? await inspectStartup(tab, event.pid) {
                    let id = prompt.identity(event: event, tab: tab)
                    let state = trustJournalError == nil ? trustReceipts[id]?.state ?? "pending" : "uncertain"
                    session["runtime"] = ["state": "approval", "approvals": [prompt.card(id: id, directory: event.cwd, state: state)]]
                    session["phase"] = "waiting"; session["canSend"] = false
                    session["status"] = "等待啟動確認"; session["approvalReviewer"] = "user"
                    session["noticeReason"] = "permission"; session["noticeID"] = id
                    session["unread"] = preferences.acknowledged[event.id] != id
                }
                sessions.append(session)
            }
            let projects: [[String: Any]] = preferences.projects.sorted {
                if $0.pinned != $1.pinned { return $0.pinned }
                if $0.lastOpenedAt != $1.lastOpenedAt { return $0.lastOpenedAt > $1.lastOpenedAt }
                return $0.id < $1.id
            }.map { project in
                ["id": project.id, "name": project.name, "directory": project.directory,
                 "tool": project.tool, "toolName": project.toolName, "pinned": project.pinned,
                 "lastOpenedAt": project.lastOpenedAt, "mode": project.session?.mode.rawValue ?? "new",
                 "sessionID": project.session?.sessionID ?? ""]
            }
            return ["sessions": sessions, "projects": projects, "defaultDirectory": preferences.lastDirectory]
        }
        if method == "directories" { return try webDirectories(params["directory"] as? String ?? "~") }
        if method == "createDirectory" { return try webCreateDirectory(params) }
        if method == "launch" { return try await webLaunch(params) }
        if method == "brainOpen" {
            guard Set(params.keys).isSubset(of: ["launchID", "threadID", "_deadline"]),
                  let identity = params["launchID"] as? String, UUID(uuidString: identity) != nil else {
                throw PilotError(message: "Brain 啟動識別不正確。")
            }
            if let row = rows.first(where: { $0.event?.id == identity }), let tab = row.tab, row.event?.alive == true {
                return ["opened": true, "binding": tab.id, "session": identity]
            }
            guard !acting, !webSending else { throw PilotError(message: "正在處理上一個操作，請稍後再試。") }
            if let deadline = params["_deadline"] as? Double, deadline <= Date().timeIntervalSince1970 {
                throw PilotError(message: "Brain 開啟請求已過期。")
            }
            guard !FileManager.default.fileExists(atPath: store.appendingPathComponent("sessions/\(identity).json").path) else {
                throw PilotError(message: "這次 Brain 開啟已有紀錄，請重新確認原視窗。")
            }
            let thread = params["threadID"] as? String ?? ""
            guard thread.isEmpty || UUID(uuidString: thread) != nil else { throw PilotError(message: "Brain 對話識別不正確。") }
            let directory = try webDirectory(FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("brain").path)
            acting = true
            defer { acting = false }
            let tab = try await createTask(tool: "codex", directory: directory.path, label: "Brain · 個人助理",
                session: AgentSession(mode: thread.isEmpty ? .new : .specific, sessionID: thread),
                launchIdentity: identity, remember: false)
            return ["opened": true, "binding": tab.id, "session": identity]
        }
        guard let session = params["session"] as? String,
              let row = rows.first(where: { $0.webID == session }) else {
            throw PilotError(message: "工作已關閉，請重新選擇。")
        }
        if method == "ack" {
            if let event = row.event, startupEligible(event), let tab = row.tab,
               let prompt = try? await inspectStartup(tab, event.pid),
               let notice = params["noticeID"] as? String, notice == prompt.identity(event: event, tab: tab) {
                preferences.acknowledged[event.id] = notice
                save()
                return ["acknowledged": true]
            }
            guard let event = row.event, let notice = params["noticeID"] as? String, notice == event.noticeID else {
                throw PilotError(message: "有新的待處理通知，請先查看最新內容。")
            }
            markRead(row)
            return ["acknowledged": true]
        }
        guard let tab = row.tab, params["binding"] as? String == tab.id else {
            throw PilotError(message: "原本的 Terminal 已關閉或移動，請重新選擇工作。")
        }
        if method == "read" { return ["text": try await readTerminal(tab)] }
        if method == "terminalInput" {
            guard Set(params.keys).isSubset(of: ["session", "binding", "text", "requestID", "_deadline"]),
                  row.event == nil,
                  let requestID = params["requestID"] as? String, !requestID.isEmpty, requestID.count <= 80,
                  let text = params["text"] as? String, text.count <= 8_000,
                  !text.unicodeScalars.contains(where: { ($0.value < 32 && $0.value != 10 && $0.value != 9) || $0.value == 127 }) else {
                throw PilotError(message: "此工作不是一般 Terminal，或輸入資料不正確，未送出。")
            }
            let fingerprint = TerminalInputJournal.fingerprint(session: session, binding: tab.id, text: text)
            if let result = try terminalInputJournal.previous(requestID, fingerprint: fingerprint) { return result }
            guard !webSending, !acting else { throw PilotError(message: "正在處理上一個操作，請稍後再送出。") }
            if let deadline = params["_deadline"] as? Double, deadline <= Date().timeIntervalSince1970 {
                throw PilotError(message: "送出請求已過期，未輸入 Terminal。")
            }
            webSending = true
            defer { webSending = false }
            try terminalInputJournal.record(requestID, fingerprint: fingerprint, sent: false)
            try await sendToTerminal(tab, text)
            try terminalInputJournal.record(requestID, fingerprint: fingerprint, sent: true)
            return ["sent": true, "uncertain": false]
        }
        if method == "closePreview" || method == "close" {
            let targets = rows.filter { $0.tab?.windowID == tab.windowID && $0.tab?.terminalPID == tab.terminalPID }
            let bindings = targets.compactMap { $0.tab?.id }.sorted()
            if method == "closePreview" {
                return ["bindings": bindings, "targets": targets.map { ["title":$0.title, "status":$0.status] }]
            }
            guard !acting, !webSending, let expected = params["bindings"] as? [String], expected.sorted() == bindings,
                  Set(expected).count == expected.count, !expected.isEmpty else {
                throw PilotError(message: "視窗分頁或工作狀態已改變，請重新確認要關閉的工作。")
            }
            if let deadline = params["_deadline"] as? Double, deadline <= Date().timeIntervalSince1970 {
                throw PilotError(message: "關閉請求已過期，未停止工作。")
            }
            acting = true
            defer { acting = false }
            try await closeTerminal(targets.compactMap(\.tab))
            readEvents()
            for target in targets {
                if let id = target.event?.id, let latest = events.first(where: { $0.id == id }) {
                    preferences.acknowledged[id] = latest.noticeID
                }
            }
            save(); await refresh()
            return ["closed": true]
        }
        if method == "approvalUpgrade" {
            guard !webSending, !acting, let event = row.event, event.tool == "codex", event.alive,
                  event.codexSocket?.isEmpty != false, let thread = event.codexThreadID,
                  UUID(uuidString: thread) != nil else {
                throw PilotError(message: "此工作無法續接，或已啟用手機核准。")
            }
            if let deadline = params["_deadline"] as? Double, deadline <= Date().timeIntervalSince1970 {
                throw PilotError(message: "續接請求已過期，未停止工作。")
            }
            acting = true
            defer { acting = false }
            let projectID = preferences.projectBindings[tab.id]
            try await stopForResume(tab, event.pid)
            let newTab = try await createTask(tool: "codex", directory: event.cwd, label: row.title,
                projectID: projectID, session: AgentSession(mode: .specific, sessionID: thread), codexHome: event.codexHome)
            return ["opened": true, "binding": newTab.id, "tool": "codex"]
        }
        if method == "startupDecision" {
            guard Set(params.keys).isSubset(of: ["session", "binding", "approval", "requestID", "action", "_deadline"]),
                  !webSending, !acting, let event = row.event, startupEligible(event),
                  let approval = params["approval"] as? String, approval.hasPrefix("startup:"),
                  let requestID = params["requestID"] as? String, !requestID.isEmpty, requestID.count <= 200,
                  let action = params["action"] as? String else {
                throw PilotError(message: "啟動確認已過期或工作已更換，未送出。")
            }
            if let error = trustJournalError { throw PilotError(message: error) }
            if let receipt = trustReceipts[approval] {
                guard receipt.action == action, receipt.requestID == requestID else {
                    throw PilotError(message: "這個確認已經送出，請等待更新。")
                }
                return ["submitted": receipt.state == "submitting", "uncertain": receipt.state == "uncertain", "resolved": false]
            }
            webSending = true
            defer { webSending = false }
            guard !trustReceipts.values.contains(where: { $0.requestID == requestID }),
                  let prompt = try await inspectStartup(tab, event.pid),
                  approval == prompt.identity(event: event, tab: tab), prompt.actions.contains(action) else {
                throw PilotError(message: "啟動畫面或選項已改變，請查看最新確認。")
            }
            if let deadline = params["_deadline"] as? Double, deadline <= Date().timeIntervalSince1970 {
                throw PilotError(message: "確認請求已過期，未送出。")
            }
            trustReceipts[approval] = ClaudeTrustReceipt(requestID: requestID, action: action, state: "uncertain")
            try saveTrustReceipts()
            try await answerStartup(tab, event.pid, prompt.text, action)
            trustReceipts[approval]?.state = "submitting"
            try saveTrustReceipts()
            return ["submitted": true, "resolved": false, "uncertain": false]
        }
        if method == "claudeTrustDecision" {
            guard !webSending, !acting, let event = row.event, event.tool == "claude", event.alive,
                  let approval = params["approval"] as? String,
                  approval == ClaudeTrustPrompt.identity(event: event, tab: tab),
                  let requestID = params["requestID"] as? String, !requestID.isEmpty, requestID.count <= 200,
                  let action = params["action"] as? String, ["accept", "decline"].contains(action) else {
                throw PilotError(message: "資料夾確認已過期或工作已更換，未送出。")
            }
            if let error = trustJournalError { throw PilotError(message: error) }
            if let receipt = trustReceipts[approval] {
                guard receipt.action == action, receipt.requestID == requestID else {
                    throw PilotError(message: "這個資料夾確認已有送出紀錄，請等待更新，勿重複送出。")
                }
                return ["submitted": receipt.state == "submitting", "uncertain": receipt.state == "uncertain", "resolved": false]
            }
            guard !trustReceipts.values.contains(where: { $0.requestID == requestID }), trustEligible(event) else {
                throw PilotError(message: "此確認識別碼已使用，或 Claude 已完成啟動。")
            }
            webSending = true
            defer { webSending = false }
            guard try await inspectClaudeTrust(tab, event.pid, event.cwd) != nil else {
                throw PilotError(message: "Claude 已不在這個資料夾確認畫面，未送出。")
            }
            if let deadline = params["_deadline"] as? Double, deadline <= Date().timeIntervalSince1970 {
                throw PilotError(message: "確認請求已過期，未送出。")
            }
            trustReceipts[approval] = ClaudeTrustReceipt(requestID: requestID, action: action, state: "uncertain")
            try saveTrustReceipts()
            try await answerClaudeTrust(tab, event.pid, event.cwd, action)
            trustReceipts[approval]?.state = "submitting"
            try saveTrustReceipts()
            return ["submitted": true, "resolved": false, "uncertain": false]
        }
        guard method == "send" else { throw PilotError(message: "不支援這個操作。") }
        guard let event = row.event, ["codex", "claude"].contains(event.tool) else {
            throw PilotError(message: "這個工作尚未連接可回覆的 agent，請在電腦操作。")
        }
        guard !webSending, !acting else { throw PilotError(message: "正在處理上一個操作，請稍後再送出。") }
        guard event.alive, !["waiting", "ended", "error"].contains(event.phase) else {
            throw PilotError(message: "\(event.toolName) 需要在電腦完成確認，或已結束。這次未送出。")
        }
        if trustEligible(event), try await inspectClaudeTrust(tab, event.pid, event.cwd) != nil {
            throw PilotError(message: "Claude 正在等待資料夾確認，請使用確認卡片，未送出文字。")
        }
        if startupEligible(event), try await inspectStartup(tab, event.pid) != nil {
            throw PilotError(message: "Codex 正在等待啟動確認，請使用確認卡片，未送出文字。")
        }
        guard let text = params["text"] as? String, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              text.count <= 8_000, !text.unicodeScalars.contains(where: { ($0.value < 32 && $0.value != 10 && $0.value != 9) || $0.value == 127 }) else {
            throw PilotError(message: "請輸入 1 至 8,000 字的文字，不能包含終端控制字元。")
        }
        webSending = true
        defer { webSending = false }
        if let deadline = params["_deadline"] as? Double, deadline <= Date().timeIntervalSince1970 {
            throw PilotError(message: "送出請求已過期，未送出。請重新連線後再試。")
        }
        try await sendToAgent(tab, event.pid, event.tool, text)
        // UserPromptSubmit/progress hooks clear the notice. HTTP delivery alone is not an AI acknowledgement.
        return ["sent": true]
    }
    private func trustEligible(_ event: AgentEvent) -> Bool {
        event.tool == "claude" && event.alive && event.phase == "connected" && (event.claudeSessionID ?? "").isEmpty
    }
    private func startupEligible(_ event: AgentEvent) -> Bool {
        event.tool == "codex" && event.alive && event.phase == "connected" && (event.codexThreadID ?? "").isEmpty
    }
    private func saveTrustReceipts() throws {
        let file = store.appendingPathComponent("claude-trust-receipts.json")
        try JSONEncoder().encode(trustReceipts).write(to: file, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
    }
    func save() {
        do {
            let data = try JSONEncoder().encode(preferences)
            let file = store.appendingPathComponent("preferences.json")
            try data.write(to: file, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
        } catch { self.error = "設定未儲存：\(error.localizedDescription)" }
    }
    func readEvents() {
        let directory = store.appendingPathComponent("sessions")
        guard let files = try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil) else { events = []; return }
        events = files.filter { $0.pathExtension == "json" }.compactMap {
            guard let data = try? Data(contentsOf: $0) else { return nil }
            return try? JSONDecoder().decode(AgentEvent.self, from: data)
        }.sorted { $0.createdAt > $1.createdAt }
    }
    func refresh(force: Bool = false) async {
        guard !refreshing, !acting else { return }
        readEvents()
        if pollingPaused && !force { rebuild(); return }
        refreshing = true
        defer { refreshing = false }
        do {
            tabs = try await snapshot()
            if force { error = nil }
            pollingPaused = false
            selection.formIntersection(Set(tabs.map(\.id)))
            rebuild()
        } catch {
            self.error = error.localizedDescription
            pollingPaused = true // Do not repeatedly prompt if Automation was denied.
            rebuild()
        }
    }
    func rebuild() {
        let before = preferences
        var output: [Row] = []
        let now = Date().timeIntervalSince1970
        // Work rows belong to open Terminal tabs. Persisted notices can outlive
        // their windows, but must not keep a closed work in the list or badge.
        // A failed snapshot retains the last known tabs in refresh().
        for tab in tabs {
            let event = events.first { event in
                guard event.tty == tab.tty else { return false }
                return preferences.bindings[event.id] == tab.id || event.alive
            }
            if let event {
                preferences.bindings[event.id] = tab.id
            }
            if preferences.tabActivity[tab.id] == nil { preferences.tabActivity[tab.id] = event?.updatedAt ?? now }
            let label = preferences.aliases[tab.id] ?? event?.label ?? tab.title
            output.append(Row(tab: tab, event: event, title: label, unread: isUnread(event), activityAt: preferences.tabActivity[tab.id] ?? 0))
        }
        rows = output.sorted(by: Row.ordered)
        onCount?(unreadCount)
        if preferences != before { save() }
    }
    func isUnread(_ event: AgentEvent?) -> Bool {
        guard let event, !event.noticeID.isEmpty else { return false }
        return preferences.acknowledged[event.id] != event.noticeID
    }
    func markRead(_ row: Row) {
        guard let event = row.event, !event.noticeID.isEmpty else { return }
        // A row is a snapshot of the notice the user chose to view. A newer notice
        // can arrive while Terminal is being raised; never acknowledge that one.
        readEvents()
        guard events.first(where: { $0.id == event.id })?.noticeID == event.noticeID else {
            rebuild(); return
        }
        preferences.acknowledged[event.id] = event.noticeID
        save(); rebuild()
    }
    func rename(_ row: Row, name: String) {
        let name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        preferences.aliases[row.id] = name.isEmpty ? nil : name
        if !name.isEmpty, let project = project(for: row) { renameProject(project, name: name) }
        save(); rebuild()
    }
    @discardableResult
    func rememberProject(tool: String, directory: String, name: String, pinned: Bool = false,
                         projectID: String? = nil, openedAt: Double = Date().timeIntervalSince1970,
                         session: AgentSession? = nil) -> SavedProject {
        let directory = URL(fileURLWithPath: directory).standardizedFileURL.path
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let name = trimmed.isEmpty ? URL(fileURLWithPath: directory).lastPathComponent : trimmed
        let index = preferences.projects.firstIndex {
            if let projectID { return $0.id == projectID }
            return $0.directory == directory && $0.tool == tool && $0.name == name
        }
        if let index {
            preferences.projects[index].lastOpenedAt = openedAt
            preferences.projects[index].name = name
            preferences.projects[index].directory = directory
            preferences.projects[index].tool = tool
            preferences.projects[index].session = session
            preferences.projects[index].pinned = preferences.projects[index].pinned || pinned
            return preferences.projects[index]
        }
        var project = SavedProject(name: name, directory: directory, tool: tool, pinned: pinned,
                                   lastOpenedAt: openedAt)
        project.session = session
        preferences.projects.append(project)
        return project
    }
    func saveProject(_ row: Row, directory: String, name: String, pinned: Bool, session: AgentSession? = nil) {
        guard validDirectory(directory) else { return }
        let existing = project(for: row)
        let tool = existing?.tool ?? (["claude", "codex"].contains(row.event?.tool ?? "") ? row.event!.tool : "terminal")
        let session = session ?? existing?.session
        guard !["codex", "claude"].contains(tool) || validSession(session) else { return }
        let project = rememberProject(tool: tool, directory: directory, name: name, pinned: pinned,
                                      projectID: existing?.id, openedAt: existing?.lastOpenedAt ?? Date().timeIntervalSince1970,
                                      session: session)
        if let index = preferences.projects.firstIndex(where: { $0.id == project.id }) { preferences.projects[index].pinned = pinned }
        preferences.projectBindings[row.id] = project.id
        preferences.aliases[row.id] = project.name
        save(); rebuild()
        projectToSave = nil
    }
    func toggleProjectPin(_ project: SavedProject) {
        guard let index = preferences.projects.firstIndex(where: { $0.id == project.id }) else { return }
        preferences.projects[index].pinned.toggle(); save()
    }
    func renameProject(_ project: SavedProject, name: String) {
        let name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, let index = preferences.projects.firstIndex(where: { $0.id == project.id }) else { return }
        preferences.projects[index].name = name
        for (tabID, projectID) in preferences.projectBindings where projectID == project.id {
            preferences.aliases[tabID] = name
        }
        save(); rebuild()
    }
    func removeProject(_ project: SavedProject) {
        preferences.projects.removeAll { $0.id == project.id }
        preferences.projectBindings = preferences.projectBindings.filter { $0.value != project.id }
        save()
    }
    func changeProjectDirectory(_ project: SavedProject, directory: String) {
        guard validDirectory(directory), let index = preferences.projects.firstIndex(where: { $0.id == project.id }) else { return }
        preferences.projects[index].directory = URL(fileURLWithPath: directory).standardizedFileURL.path
        save()
    }
    func reopenProject(_ project: SavedProject) {
        guard !acting, validDirectory(project.directory) else { return }
        error = nil
        if project.isAgent {
            projectToOpen = project
            return
        }
        newTask(tool: project.tool, directory: project.directory, label: project.name,
                projectID: project.id)
    }
    func openProject(_ project: SavedProject, session: AgentSession) {
        guard projectToOpen?.id == project.id,
              let current = preferences.projects.first(where: { $0.id == project.id }), current.isAgent else { return }
        newTask(tool: current.tool, directory: current.directory, label: current.name,
                projectID: current.id, session: session)
    }
    func configureProject(_ project: SavedProject, session: AgentSession) {
        guard project.isAgent, validSession(session), let index = preferences.projects.firstIndex(where: { $0.id == project.id }) else { return }
        preferences.projects[index].session = session
        save(); projectToConfigure = nil
    }
    func validSession(_ session: AgentSession?) -> Bool {
        guard session?.isValid != false else {
            error = "請輸入有效的 session ID 或對話名稱（不可留白或以 - 開頭）。"; return false
        }
        return true
    }
    func validDirectory(_ directory: String) -> Bool {
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: directory, isDirectory: &isDirectory), isDirectory.boolValue else {
            error = "專案資料夾不存在：\(directory)。請重新選擇資料夾。"; return false
        }
        return true
    }
    func togglePin() {
        pinned.toggle(); preferences.pinned = pinned; save(); onPin?(pinned)
    }
    func perform(_ operation: @escaping () async throws -> Void) {
        guard !acting else { return }
        acting = true
        Task {
            var failure: String?
            error = nil
            do { try await operation(); failure = error }
            catch { failure = error.localizedDescription }
            acting = false
            await refresh(force: true)
            if let failure { self.error = failure }
        }
    }
    func focus(_ row: Row) { focus(row, click: RecallClick.current) }
    func focus(_ row: Row, click: RecallClick?) {
        guard let tab = row.tab, !acting else { return }
        // A button can handle the first click and reorder the list before the
        // second click lands. The same mouse gesture must never recall two rows.
        let repeatedClick = click.flatMap { current in lastRecallClick.map { current.continues($0) } } ?? false
        lastRecallClick = click
        guard !repeatedClick else { return }
        perform {
            try await self.bringToFront(tab)
            self.preferences.tabActivity[tab.id] = Date().timeIntervalSince1970
            self.save()
            if row.unread { self.markRead(row) }
        }
    }
    func minimize(_ row: Row) {
        guard let tab = row.tab else { return }
        perform { try await TerminalBridge.minimize(tab) }
    }
    func area() -> CGRect {
        let window = NSApp.windows.first { $0.identifier?.rawValue == "pilot" }
        let screen = window?.screen ?? NSScreen.main ?? NSScreen.screens[0]
        var visible = screen.visibleFrame
        let reserved = min((window?.frame.width ?? 400) + 24, visible.width * 0.4)
        visible.size.width -= reserved
        visible = visible.insetBy(dx: 8, dy: 8)
        let top = (NSScreen.screens.first?.frame.maxY ?? screen.frame.maxY) - visible.maxY
        return CGRect(x: visible.minX, y: top, width: visible.width, height: visible.height)
    }
    func arrange() {
        let chosen = tabs.filter { selection.contains($0.id) }
        var seen: Set<Int> = []
        let unique = chosen.filter { seen.insert($0.windowID).inserted }
        guard !unique.isEmpty else { return }
        let rect = area()
        let columns = unique.count <= 2 ? unique.count : 2
        let rowCount = Int(ceil(Double(unique.count) / Double(columns)))
        let width = rect.width / Double(columns), height = rect.height / Double(rowCount)
        guard width >= 280, height >= 180 else {
            error = "選取的視窗太多，請減少數量再排列。"; return
        }
        previousLayout = unique; layoutAvailable = true
        perform {
            for (index, tab) in unique.enumerated() {
                let x = rect.minX + Double(index % columns) * width
                let y = rect.minY + Double(index / columns) * height
                try await TerminalBridge.place(tab, bounds: [Int(x), Int(y), Int(x + width - 8), Int(y + height - 8)])
            }
        }
    }
    func restoreLayout() {
        let saved = previousLayout
        perform {
            var failures: [String] = []
            for tab in saved {
                do { try await TerminalBridge.place(tab, bounds: tab.bounds, minimized: tab.minimized) }
                catch { failures.append(error.localizedDescription) }
            }
            if !failures.isEmpty { throw PilotError(message: failures.joined(separator: "\n")) }
            self.previousLayout = []; self.layoutAvailable = false
        }
    }
    func side(_ row: Row, right: Bool) {
        guard let tab = row.tab else { return }
        previousLayout = [tab]; layoutAvailable = true
        let rect = area(), half = area().width / 2
        let x = right ? rect.minX + half : rect.minX
        perform { try await TerminalBridge.place(tab, bounds: [Int(x), Int(rect.minY), Int(x + half - 8), Int(rect.maxY)]) }
    }
    func newTask(tool: String, directory: String, label: String, pinned: Bool = false,
                 projectID: String? = nil, session: AgentSession? = nil) {
        guard !acting, validDirectory(directory) else { return }
        guard !["codex", "claude"].contains(tool) || validSession(session) else { return }
        perform {
            _ = try await self.createTask(tool: tool, directory: directory, label: label,
                                          pinned: pinned, projectID: projectID, session: session)
        }
    }
    private func createTask(tool: String, directory: String, label: String, pinned: Bool = false,
                            projectID: String? = nil, session: AgentSession? = nil, codexHome: String? = nil,
                            launchIdentity: String? = nil, remember: Bool = true) async throws -> TerminalTab {
        let directory = URL(fileURLWithPath: directory).standardizedFileURL.path
        let command: String
        if tool == "terminal" {
            command = "cd -- " + TerminalBridge.shellQuote(directory)
        } else {
            guard ["claude", "codex", "demo"].contains(tool), let resource = Bundle.main.resourceURL else {
                throw PilotError(message: "無法啟動此工作類型。")
            }
            if tool != "demo" { try validateTool(tool) }
            let python = try RuntimeSupport.python(resource: resource)
            var args = ["/usr/bin/env", "PYTHONNOUSERSITE=1", "PYTHONDONTWRITEBYTECODE=1",
                        python, "-E", "-s", "-B", resource.appendingPathComponent("bridge.py").path]
            if tool == "demo" { args += ["simulate", "--store", store.path] }
            else {
                args += ["launch", tool, "--store", store.path, "--cwd", directory, "--label", label]
                if let launchIdentity { args += ["--session", launchIdentity] }
                if tool == "codex", let codexHome, !codexHome.isEmpty { args += ["--codex-home", codexHome] }
                let extra = session?.arguments(for: tool) ?? []
                if !extra.isEmpty { args += ["--"] + extra }
            }
            command = args.map(TerminalBridge.shellQuote).joined(separator: " ")
        }
        let tab = try await launch(command, label)
        let now = Date().timeIntervalSince1970
        if remember { preferences.lastDirectory = directory }
        preferences.tabActivity[tab.id] = now
        if tool != "demo" && remember {
            let project = rememberProject(tool: tool, directory: directory, name: label,
                                          pinned: pinned, projectID: projectID, openedAt: now, session: session)
            preferences.projectBindings[tab.id] = project.id
            preferences.aliases[tab.id] = project.name
        }
        if !remember { preferences.aliases[tab.id] = label }
        save()
        if let projectID, projectToOpen?.id == projectID { projectToOpen = nil }
        showingProjects = false
        return tab
    }
    private func webDirectory(_ raw: String) throws -> URL {
        guard raw.count <= 4096, !raw.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }),
              raw == "~" || raw.hasPrefix("~/") || raw.hasPrefix("/") else {
            throw PilotError(message: "請選擇資料夾，或輸入完整路徑（可使用 ~/）。")
        }
        let path = (raw as NSString).expandingTildeInPath
        let url = URL(fileURLWithPath: path).standardizedFileURL
        var directory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: url.path, isDirectory: &directory), directory.boolValue else {
            throw PilotError(message: "資料夾不存在，請重新選擇。")
        }
        return url
    }
    private func webDirectories(_ raw: String) throws -> [String: Any] {
        let url = try webDirectory(raw)
        let directories = try FileManager.default.contentsOfDirectory(at: url, includingPropertiesForKeys: [.isDirectoryKey],
                                                                       options: [.skipsHiddenFiles]).filter {
            (try? $0.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true
        }.sorted { $0.lastPathComponent.localizedStandardCompare($1.lastPathComponent) == .orderedAscending }
        return ["directory": url.path, "parent": url.path == "/" ? "" : url.deletingLastPathComponent().path,
                "directories": directories.prefix(300).map { ["name": $0.lastPathComponent, "path": $0.path] },
                "truncated": directories.count > 300]
    }
    private func webCreateDirectory(_ params: [String: Any]) throws -> [String: Any] {
        guard let rawParent = params["parent"] as? String, let rawName = params["name"] as? String else {
            throw PilotError(message: "請選擇上層資料夾並輸入名稱。")
        }
        if let deadline = params["_deadline"] as? Double, deadline <= Date().timeIntervalSince1970 {
            throw PilotError(message: "建立請求已過期，未新增資料夾。")
        }
        let name = rawName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, name != ".", name != "..", name.utf8.count <= 255,
              !name.contains("/"), !name.contains("\\"),
              !rawName.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
            throw PilotError(message: "請輸入單一資料夾名稱，不能包含斜線、換行、. 或 ..，長度不可超過 255 位元組。")
        }
        let parent = try webDirectory(rawParent).resolvingSymlinksInPath()
        let child = parent.appendingPathComponent(name, isDirectory: true)
        guard child.path.utf8.count < 4096 else { throw PilotError(message: "資料夾完整路徑過長。") }
        // Atomic mkdir rejects existing folders, files and symlinks. Never use a shell
        // or create missing ancestors, and never silently reuse an existing project.
        guard mkdir(child.path, 0o755) == 0 else {
            switch errno {
            case EEXIST: throw PilotError(message: "同名資料夾或檔案已存在，請從清單選用資料夾或換個名稱。")
            case EACCES, EPERM, EROFS: throw PilotError(message: "無法在這個位置新增資料夾，請選擇有寫入權限的位置。")
            case ENOSPC: throw PilotError(message: "電腦儲存空間不足，無法新增資料夾。")
            default: throw PilotError(message: "新增資料夾失敗，請重新讀取上層資料夾後再試。")
            }
        }
        return ["created": true, "directory": child.path]
    }
    private func webLaunch(_ params: [String: Any]) async throws -> [String: Any] {
        guard !acting, !webSending else { throw PilotError(message: "正在處理上一個操作，請稍後再試。") }
        if let deadline = params["_deadline"] as? Double, deadline <= Date().timeIntervalSince1970 {
            throw PilotError(message: "開啟請求已過期，這次未開啟。")
        }
        var project: SavedProject?
        if let projectID = params["projectID"] as? String, !projectID.isEmpty {
            guard let saved = preferences.projects.first(where: { $0.id == projectID }) else {
                throw PilotError(message: "專案紀錄已移除，請重新選擇。")
            }
            project = saved
        }
        let tool = project?.tool ?? params["tool"] as? String ?? ""
        guard ["terminal", "codex", "claude"].contains(tool) else { throw PilotError(message: "請選擇 Terminal、Codex 或 Claude Code。") }
        let directory = try webDirectory(project?.directory ?? params["directory"] as? String ?? "")
        let label = (project?.name ?? params["name"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard label.count <= 120, !label.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
            throw PilotError(message: "工作名稱限 120 字，不能包含換行或控制字元。")
        }
        var session: AgentSession?
        if tool != "terminal" {
            guard let mode = AgentStartMode(rawValue: params["mode"] as? String ?? ""), mode != .picker else {
                throw PilotError(message: "請選擇新對話、續接最近或指定對話。")
            }
            let id = params["sessionID"] as? String ?? ""
            session = AgentSession(mode: mode, sessionID: id)
            guard id.count <= 256, session!.isValid else { throw PilotError(message: "請輸入有效的 session ID 或對話名稱。") }
        }
        acting = true
        let tab: TerminalTab
        do {
            tab = try await createTask(tool: tool, directory: directory.path, label: label,
                                       pinned: project?.pinned ?? params["pinned"] as? Bool ?? false,
                                       projectID: project?.id, session: session)
        } catch {
            acting = false; throw error
        }
        acting = false
        await refresh(force: true)
        return ["opened": true, "binding": tab.id, "tool": tool,
                "projectID": preferences.projectBindings[tab.id] ?? ""]
    }
}
