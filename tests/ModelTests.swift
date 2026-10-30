import Foundation
import Darwin

@main struct ModelTests {
    @MainActor static func main() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("pilot-model-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        var checks = 0
        func check(_ condition: @autoclosure () -> Bool, _ message: String) {
            guard condition() else { fatalError(message) }
            checks += 1
        }
        func tab(_ index: Int) -> TerminalTab {
            TerminalTab(tty: "/dev/test\(index)", windowID: index, title: "Window \(index)",
                        minimized: false, bounds: [0, 0, 400, 300], selected: true, terminalPID: 123)
        }
        func event(_ index: Int, notice: Double, updated: Double, phase: String = "complete") -> AgentEvent {
            AgentEvent(id: "event\(index)", tty: tab(index).tty, tool: "claude", cwd: root.path,
                       label: "Event \(index)", pid: Int(getpid()), phase: phase, createdAt: Double(index),
                       updatedAt: updated, noticeID: "notice\(notice)", noticeAt: notice, noticePhase: phase)
        }
        func writeEvent(_ value: AgentEvent, store: URL) throws {
            let sessions = store.appendingPathComponent("sessions")
            try FileManager.default.createDirectory(at: sessions, withIntermediateDirectories: true)
            try JSONEncoder().encode(value).write(to: sessions.appendingPathComponent("\(value.id).json"))
        }
        func waitForAction(_ model: PilotModel) async throws {
            for _ in 0..<1000 {
                if !model.acting && !model.refreshing { return }
                try await Task.sleep(nanoseconds: 1_000_000)
            }
            fatalError("Model action did not finish")
        }

        // Missing optional tools fail before opening a Terminal or changing project history.
        var missingLaunches = 0
        let missingToolModel = PilotModel(validateTool: { _ in throw PilotError(message: "Install the optional tool") },
                                 storeURL: root.appendingPathComponent("missing-tools"), snapshot: { [] },
                                 launch: { _, _ in missingLaunches += 1; return tab(999) })
        missingToolModel.newTask(tool: "codex", directory: root.path, label: "Missing")
        try await waitForAction(missingToolModel)
        check(missingLaunches == 0 && missingToolModel.preferences.projects.isEmpty && missingToolModel.error == "Install the optional tool", "Missing tool opened a dead work or changed history")
        missingToolModel.newTask(tool: "terminal", directory: root.path, label: "Plain")
        try await waitForAction(missingToolModel)
        check(missingLaunches == 1 && missingToolModel.preferences.projects.count == 1, "Missing optional agent blocked plain Terminal")

        // A real v0.1 preferences payload must migrate without losing user state.
        let old = #"{"acknowledged":{"event1":"old"},"bindings":{"event1":"tab1"},"aliases":{"tab1":"Named"},"order":["tab1"],"dismissed":["past"],"lastDirectory":"/tmp","pinned":false}"#
        let decoded = try JSONDecoder().decode(Preferences.self, from: Data(old.utf8))
        check(decoded.acknowledged["event1"] == "old" && decoded.aliases["tab1"] == "Named", "Migration lost unread/alias data")
        check(decoded.bindings["event1"] == "tab1" && decoded.dismissed.contains("past"), "Migration lost bindings/dismissal")
        check(decoded.projects.isEmpty && decoded.tabActivity.isEmpty && !decoded.pinned && decoded.lastDirectory == "/tmp", "Migration defaults are incorrect")
        let roundTrip = try JSONDecoder().decode(Preferences.self, from: JSONEncoder().encode(decoded))
        check(roundTrip == decoded, "Preferences round trip changed state")

        let sortStore = root.appendingPathComponent("sorting")
        var visible = [tab(1), tab(2), tab(3)]
        let sorter = PilotModel(validateTool: { _ in }, storeURL: sortStore, snapshot: { visible })
        try writeEvent(event(1, notice: 100, updated: 900, phase: "waiting"), store: sortStore)
        try writeEvent(event(2, notice: 200, updated: 200), store: sortStore)
        try writeEvent(event(3, notice: 0, updated: 1000, phase: "running"), store: sortStore)
        sorter.preferences.acknowledged["event3"] = "notice0.0"
        try writeEvent(event(4, notice: 300, updated: 300, phase: "error"), store: sortStore)
        await sorter.refresh()
        check(sorter.rows.map { $0.event!.id } == ["event2", "event1", "event3"], "Unread/newest ordering included a closed window")
        check(sorter.unreadCount == 2, "Unread count included a closed window")
        let timestamps = sorter.preferences.tabActivity
        visible.reverse()
        await sorter.refresh()
        check(sorter.rows.map { $0.event!.id } == ["event2", "event1", "event3"], "Terminal z-order changed sorting")
        check(sorter.preferences.tabActivity == timestamps, "Polling changed activity dates")
        try writeEvent(event(1, notice: 400, updated: 400, phase: "waiting"), store: sortStore)
        await sorter.refresh()
        check(sorter.rows.first?.event?.id == "event1", "New notification did not move to top")
        sorter.markRead(sorter.rows[0])
        check(sorter.rows.map { $0.event!.id } == ["event2", "event3", "event1"], "Acknowledged work did not return to activity ordering")
        check(sorter.preferences.acknowledged["event4"] == nil, "Hiding a closed work incorrectly acknowledged its notice")
        let restoredSort = PilotModel(validateTool: { _ in }, storeURL: sortStore, snapshot: { visible })
        await restoredSort.refresh()
        check(restoredSort.unreadCount == 1 && restoredSort.rows.first?.event?.id == "event2", "Restart lost acknowledgment or sorting")
        let tieA = Row(tab: tab(5), title: "A", unread: false, activityAt: 50)
        let tieB = Row(tab: tab(6), title: "B", unread: false, activityAt: 50)
        check([tieB, tieA].sorted(by: Row.ordered).map(\.id) == [tieA, tieB].sorted(by: Row.ordered).map(\.id), "Equal times are unstable")

        // Only explicit, successful Pilot recall acknowledges the selected notice.
        let readStore = root.appendingPathComponent("reading")
        var readTabs = [tab(70), tab(71)]
        var snapshotFails = false
        var focusFails = false
        var noticeDuringFocus: AgentEvent?
        var focusedIDs: [String] = []
        var badgeCount = -1
        let reader = PilotModel(validateTool: { _ in }, storeURL: readStore, snapshot: {
            if snapshotFails { throw PilotError(message: "Synthetic snapshot failure") }
            return readTabs
        }, bringToFront: { selected in
            focusedIDs.append(selected.id)
            if focusFails { throw PilotError(message: "Synthetic focus failure") }
            if let notice = noticeDuringFocus { try writeEvent(notice, store: readStore) }
        })
        reader.onCount = { badgeCount = $0 }
        try writeEvent(event(70, notice: 100, updated: 100), store: readStore)
        try writeEvent(event(71, notice: 200, updated: 200), store: readStore)
        await reader.refresh()
        readTabs[0].selected = false
        readTabs[1].selected = true
        readTabs[0].minimized = true
        readTabs.reverse()
        await reader.refresh()
        check(reader.unreadCount == 2 && reader.preferences.acknowledged.isEmpty, "External window/tab focus or minimization cleared unread")
        let chosen = reader.rows.first { $0.event?.id == "event70" }!
        focusFails = true
        reader.focus(chosen)
        try await waitForAction(reader)
        check(reader.unreadCount == 2 && reader.error == "Synthetic focus failure", "Failed recall cleared unread")
        focusFails = false
        reader.focus(chosen)
        try await waitForAction(reader)
        check(focusedIDs.last == tab(70).id && reader.preferences.acknowledged["event70"] == chosen.event!.noticeID, "Successful recall did not acknowledge its exact target")
        check(reader.unreadCount == 1 && badgeCount == 1 && reader.rows.first?.event?.id == "event71", "Recall changed other unread work or did not update badge/sort")
        let restartedReader = PilotModel(validateTool: { _ in }, storeURL: readStore, snapshot: { readTabs })
        await restartedReader.refresh()
        check(restartedReader.unreadCount == 1, "Recall acknowledgment did not persist")
        try writeEvent(event(70, notice: 300, updated: 300), store: readStore)
        await reader.refresh()
        check(reader.unreadCount == 2, "New notice while a window was already open was auto-read")
        let stale = reader.rows.first { $0.event?.id == "event70" }!
        noticeDuringFocus = event(70, notice: 400, updated: 400)
        reader.focus(stale)
        try await waitForAction(reader)
        check(reader.rows.first?.event?.noticeAt == 400 && reader.rows.first?.unread == true, "Recall swallowed a notice that arrived during focus")
        reader.markRead(stale)
        check(reader.rows.first?.unread == true && badgeCount == 2, "Stale manual acknowledgment cleared a newer notice")
        noticeDuringFocus = nil
        reader.focus(reader.rows[0])
        try await waitForAction(reader)
        check(reader.unreadCount == 1, "Explicit recall did not clear the new notice")
        let alreadyRead = reader.rows.first { $0.event?.id == "event70" }!
        noticeDuringFocus = event(70, notice: 500, updated: 500)
        reader.focus(alreadyRead)
        try await waitForAction(reader)
        check(reader.unreadCount == 2, "Opening a read row acknowledged a later notice")
        noticeDuringFocus = nil
        // Window lifetime, not notification acknowledgment or agent PID, owns a row.
        reader.selection = Set(readTabs.map(\.id))
        readTabs = []
        snapshotFails = true
        await reader.refresh()
        check(reader.rows.count == 2 && badgeCount == 2, "Failed Terminal snapshot incorrectly removed windows or badges")
        snapshotFails = false
        readTabs = [tab(71)]
        await reader.refresh(force: true)
        check(reader.rows.map(\.id) == [tab(71).id] && reader.unreadCount == 1 && badgeCount == 1, "Closed unread window remained in list or badge")
        check(reader.selection == [tab(71).id], "Closed window remained selected")
        var exitedAgent = event(71, notice: 200, updated: 200)
        exitedAgent.pid = Int(Int32.max)
        try writeEvent(exitedAgent, store: readStore)
        await reader.refresh()
        check(reader.rows.count == 1 && reader.rows[0].event?.alive == false && badgeCount == 1, "Agent exit removed its still-open Terminal window")
        readTabs = []
        await reader.refresh()
        check(reader.rows.isEmpty && reader.filteredRows.isEmpty && badgeCount == 0 && reader.selection.isEmpty, "Closing all windows left unread rows, selection or badges")
        check(reader.preferences.acknowledged["event70"] == "notice400.0" && reader.preferences.acknowledged["event71"] == nil, "Closing windows rewrote notice history")
        try writeEvent(event(70, notice: 600, updated: 600), store: readStore)
        await reader.refresh()
        check(reader.rows.isEmpty && badgeCount == 0, "Late event resurrected a closed Terminal window")
        let closedPhoneState = try await reader.webRequest("list", params: [:])
        check((closedPhoneState["sessions"] as? [[String: Any]])?.isEmpty == true, "Phone list retained closed unread windows")
        let closedRestart = PilotModel(validateTool: { _ in }, storeURL: readStore, snapshot: { [] })
        await closedRestart.refresh()
        check(closedRestart.rows.isEmpty && closedRestart.unreadCount == 0, "Restart resurrected persisted closed-window notices")

        // A fast recall can finish and move a row between the two mouse clicks.
        // The second click now names a different row, but belongs to the same gesture.
        let clickStore = root.appendingPathComponent("recall-clicks")
        var clickTabs = [tab(90), tab(91), tab(92)].map { value in
            var value = value; value.minimized = true; return value
        }
        var recalled: [String] = []
        let clickModel = PilotModel(validateTool: { _ in }, storeURL: clickStore, snapshot: { clickTabs }, bringToFront: { target in
            recalled.append(target.id)
            let index = clickTabs.firstIndex { $0.id == target.id }!
            clickTabs[index].minimized = false
            clickTabs.reverse() // Native window order changes while the row retains its identity.
        })
        for index in 90...92 { try writeEvent(event(index, notice: Double(400 - index), updated: 100), store: clickStore) }
        await clickModel.refresh()
        func click(_ count: Int, _ timestamp: Double, x: Double = 40) -> RecallClick {
            RecallClick(count: count, timestamp: timestamp, windowNumber: 1, location: .init(x: x, y: 60), interval: 0.5)
        }
        clickModel.focus(clickModel.rows[1], click: click(1, 10))
        try await waitForAction(clickModel)
        check(recalled == [tab(91).id] && clickModel.rows[1].id == tab(92).id,
              "Recall did not set up the moved-row double-click regression")
        clickModel.focus(clickModel.rows[1], click: click(2, 10.001))
        try await waitForAction(clickModel)
        check(recalled == [tab(91).id] && clickTabs.filter { !$0.minimized }.map(\.id) == [tab(91).id],
              "Second click recalled the unrelated window that moved under the pointer")
        check(clickModel.unreadCount == 2 && clickModel.rows[1].unread,
              "Second click acknowledged an unrelated notification")
        clickModel.focus(clickModel.rows[1], click: click(3, 10.002))
        try await waitForAction(clickModel)
        check(recalled.count == 1, "A third click bypassed the recall gesture guard")
        clickModel.focus(clickModel.rows[1], click: click(2, 20))
        try await waitForAction(clickModel)
        check(recalled == [tab(91).id, tab(92).id], "A new row double-click was swallowed")
        clickModel.focus(clickModel.rows[0], click: click(1, 30))
        try await waitForAction(clickModel)
        clickModel.focus(clickModel.rows.first { $0.id == tab(91).id }!, click: click(2, 30.001, x: 100))
        try await waitForAction(clickModel)
        check(recalled.suffix(2) == [tab(90).id, tab(91).id], "An independent click at a different location was swallowed")
        clickModel.focus(clickModel.rows.first { $0.id == tab(92).id }!, click: click(1, 30.002))
        try await waitForAction(clickModel)
        check(recalled.last == tab(92).id && recalled.count == 5, "A new single-click was blocked by a cooldown")
        clickModel.focus(clickModel.rows.first { $0.id == tab(90).id }!, click: nil)
        try await waitForAction(clickModel)
        check(recalled.last == tab(90).id && recalled.count == 6, "Non-mouse recall was blocked")

        let lifecycleStore = root.appendingPathComponent("lifecycle")
        let lifecycleModel = PilotModel(validateTool: { _ in }, storeURL: lifecycleStore, snapshot: { [tab(80)] })
        var codexEvent = event(80, notice: 100, updated: 100)
        codexEvent.tool = "codex"
        try writeEvent(codexEvent, store: lifecycleStore)
        await lifecycleModel.refresh()
        check(lifecycleModel.unreadCount == 1 && lifecycleModel.rows[0].event?.lifecycle == nil, "Legacy completion-only events stopped decoding")
        codexEvent.phase = "running"; codexEvent.noticeID = ""; codexEvent.lifecycle = "ready"
        try writeEvent(codexEvent, store: lifecycleStore)
        await lifecycleModel.refresh()
        check(lifecycleModel.unreadCount == 0 && lifecycleModel.rows[0].status == "執行中", "Submitted prompt did not clear attention and show working")
        codexEvent.phase = "waiting"; codexEvent.noticeID = "permission"; codexEvent.noticePhase = "waiting"
        try writeEvent(codexEvent, store: lifecycleStore)
        await lifecycleModel.refresh()
        check(lifecycleModel.unreadCount == 1 && lifecycleModel.rows[0].status == "等你處理", "Approval did not restore attention")
        codexEvent.phase = "running"; codexEvent.noticeID = ""
        try writeEvent(codexEvent, store: lifecycleStore)
        await lifecycleModel.refresh()
        check(lifecycleModel.unreadCount == 0 && lifecycleModel.rows[0].event?.lifecycle == "ready", "Resumed work retained attention")

        let projectStore = root.appendingPathComponent("projects")
        let directory = root.appendingPathComponent("Project ' with spaces $(literal)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        var launched: [(String, String)] = []
        var activeTabs: [TerminalTab] = []
        var failLaunch = false
        let model = PilotModel(validateTool: { _ in }, storeURL: projectStore, snapshot: { activeTabs }, launch: { command, title in
            if failLaunch { throw PilotError(message: "Synthetic launch failure") }
            launched.append((command, title))
            let created = tab(10 + launched.count)
            activeTabs.append(created)
            return created
        })
        model.newTask(tool: "terminal", directory: directory.path, label: "我的專案", pinned: true)
        model.newTask(tool: "terminal", directory: directory.path, label: "double click")
        try await waitForAction(model)
        check(launched.count == 1 && model.preferences.projects.count == 1, "Duplicate launch or missing history")
        check(launched[0].0 == "cd -- " + TerminalBridge.shellQuote(directory.path) && launched[0].1 == "我的專案", "Launch lost safe quoting or project title")
        let project = model.preferences.projects[0]
        check(project.pinned && project.directory == directory.path && project.tool == "terminal", "Project configuration was not saved")
        check(model.rows[0].title == "我的專案" && model.project(for: model.rows[0])?.id == project.id, "Opened row is not linked to its project")
        model.rename(model.rows[0], name: "新名字")
        check(model.preferences.projects[0].name == "新名字", "Row rename did not persist to project")
        try writeEvent(event(11, notice: 100, updated: 100), store: projectStore)
        await model.refresh()
        check(model.unreadCount == 1, "Saved-project closure fixture has no unread work")
        activeTabs = []
        await model.refresh()
        check(model.rows.isEmpty && model.preferences.projects.count == 1, "Closing unread Terminal retained its row or removed saved project")
        let restarted = PilotModel(validateTool: { _ in }, storeURL: projectStore, snapshot: { [] })
        check(restarted.preferences.projects[0].name == "新名字" && restarted.preferences.projects[0].pinned, "Restart lost project name or pin")
        model.reopenProject(model.preferences.projects[0])
        try await waitForAction(model)
        check(launched.count == 2 && launched[1].1 == "新名字", "One-click reopen lost name")
        check(model.preferences.projects.count == 1 && model.preferences.projects[0].id == project.id, "Reopen duplicated project")
        check(model.preferences.projects[0].lastOpenedAt > project.lastOpenedAt && model.preferences.projects[0].pinned, "Reopen did not update recency or preserve pin")

        let oldPinned = model.rememberProject(tool: "codex", directory: root.path, name: "Older pinned", pinned: true, openedAt: 1)
        let newest = model.rememberProject(tool: "claude", directory: root.path, name: "Latest", openedAt: Date().timeIntervalSince1970 + 100)
        check(model.filteredProjects.map(\.id) == [project.id, oldPinned.id, newest.id], "Pinned/recent project ordering failed")
        model.toggleProjectPin(oldPinned)
        check(model.filteredProjects.map(\.id) == [project.id, newest.id, oldPinned.id], "Unpin did not restore recent ordering")
        model.query = "Codex"
        check(model.filteredProjects.map(\.id) == [oldPinned.id], "Tool search failed")
        model.query = "$(literal)"
        check(model.filteredProjects.map(\.id) == [project.id] && model.filteredRows.count == 1, "Directory search failed")
        model.query = ""

        let countBeforeFailure = model.preferences.projects.count
        let dateBeforeFailure = model.preferences.projects[0].lastOpenedAt
        failLaunch = true
        model.reopenProject(model.preferences.projects[0])
        try await waitForAction(model)
        check(model.error == "Synthetic launch failure" && model.preferences.projects[0].lastOpenedAt == dateBeforeFailure, "Failed reopen changed recency or lost error")
        model.newTask(tool: "terminal", directory: root.path, label: "Failed")
        try await waitForAction(model)
        check(model.preferences.projects.count == countBeforeFailure, "Failed launch created history")
        failLaunch = false
        var missing = project
        missing.directory = root.appendingPathComponent("missing").path
        model.reopenProject(missing)
        check(launched.count == 2 && model.error?.contains("資料夾不存在") == true, "Missing folder was launched")
        model.changeProjectDirectory(project, directory: root.path)
        check(model.preferences.projects[0].directory == root.path, "Project folder could not be repaired")
        model.reopenProject(model.preferences.projects[0])
        try await waitForAction(model)
        check(launched.last?.0 == "cd -- " + TerminalBridge.shellQuote(root.path), "Reopen ignored changed folder")

        let external = Row(tab: tab(50), event: event(50, notice: 2, updated: 2), title: "Existing AI", unread: true)
        model.saveProject(external, directory: root.path, name: "Existing AI", pinned: true)
        let saved = model.project(for: external)!
        check(saved.tool == "claude" && saved.pinned, "Existing AI was not saved with its launch type")
        model.saveProject(external, directory: root.path, name: "Edited AI", pinned: false)
        check(model.project(for: external)?.id == saved.id && model.project(for: external)?.pinned == false, "Editing existing saved project duplicated it or lost pin change")
        check(model.project(for: external)?.lastOpenedAt == saved.lastOpenedAt, "Editing changed last-opened date")
        model.removeProject(model.preferences.projects.first { $0.id == project.id }!)
        check(!model.preferences.projects.contains { $0.id == project.id } && !model.preferences.projectBindings.values.contains(project.id), "Removal left stale project bindings")
        check(!model.rows.isEmpty && FileManager.default.fileExists(atPath: directory.path), "Removing history affected Terminal or project files")
        let afterRemoval = PilotModel(validateTool: { _ in }, storeURL: projectStore, snapshot: { activeTabs })
        await afterRemoval.refresh()
        check(!afterRemoval.preferences.projects.contains { $0.id == project.id }, "Removed project reappeared after restart")

        // Reopening an AI project always asks; only confirmation launches or changes preferences.
        let legacyProject = #"{"id":"legacy","name":"Legacy","directory":"/tmp","tool":"codex","pinned":true,"lastOpenedAt":1}"#
        let legacy = try JSONDecoder().decode(SavedProject.self, from: Data(legacyProject.utf8))
        check(legacy.session == nil && legacy.claudeSession == nil && legacy.pinned, "Legacy projects no longer decode")
        let oldResumeProject = #"{"id":"resume-old","name":"Old","directory":"/tmp","tool":"codex","pinned":false,"lastOpenedAt":1,"codexSession":{"mode":"specific","sessionID":"old-id","allProjects":false}}"#
        let oldResume = try JSONDecoder().decode(SavedProject.self, from: Data(oldResumeProject.utf8))
        check(oldResume.session == AgentSession(mode: .specific, sessionID: "old-id"), "Existing Codex resume preference was lost")
        model.newTask(tool: "codex", directory: root.path, label: "Resume", session: AgentSession())
        try await waitForAction(model)
        let resumeProject = model.preferences.projects.first { $0.name == "Resume" }!
        let beforeChoice = model.preferences
        let beforeChoiceCount = launched.count
        model.reopenProject(resumeProject)
        check(model.projectToOpen == resumeProject && !model.acting && launched.count == beforeChoiceCount, "Opening AI project bypassed choice")
        check(model.preferences == beforeChoice, "Presenting choice changed history")
        model.projectToOpen = nil
        model.openProject(resumeProject, session: AgentSession(mode: .latest))
        check(launched.count == beforeChoiceCount && model.preferences == beforeChoice, "Cancelled choice launched or saved settings")

        model.reopenProject(resumeProject)
        model.openProject(resumeProject, session: AgentSession(mode: .picker))
        model.openProject(resumeProject, session: AgentSession(mode: .new))
        try await waitForAction(model)
        check(launched.count == beforeChoiceCount + 1 && launched.last!.0.hasSuffix("'--' 'resume'"), "Picker did not override stored new mode, or double submission launched twice")
        check(model.projectToOpen == nil && model.preferences.projects.first { $0.id == resumeProject.id }?.session?.mode == .picker, "Success did not dismiss choice and remember selection")

        let specific = AgentSession(mode: .specific, sessionID: "my session ' $(literal)")
        let modes: [(AgentSession, String)] = [
            (AgentSession(mode: .latest), "'--' 'resume' '--last'"),
            (specific, "'--' 'resume' " + TerminalBridge.shellQuote(specific.sessionID)),
            (AgentSession(mode: .picker, allProjects: true), "'--' 'resume' '--all'")
        ]
        for (session, suffix) in modes {
            let current = model.preferences.projects.first { $0.id == resumeProject.id }!
            model.reopenProject(current)
            model.openProject(current, session: session)
            try await waitForAction(model)
            check(launched.last!.0.hasSuffix(suffix), "Codex reopen lost chosen resume arguments")
            let restoredResume = PilotModel(validateTool: { _ in }, storeURL: projectStore, snapshot: { [] })
            check(restoredResume.preferences.projects.first { $0.id == resumeProject.id }?.session == session, "Restart lost chosen Codex resume target")
        }
        check(model.preferences.projects.filter { $0.id == resumeProject.id }.count == 1, "Changing resume mode duplicated project")
        let beforeFailedChoice = model.preferences
        let currentResume = model.preferences.projects.first { $0.id == resumeProject.id }!
        model.reopenProject(currentResume)
        failLaunch = true
        model.openProject(currentResume, session: AgentSession())
        try await waitForAction(model)
        check(model.projectToOpen == currentResume && model.error == "Synthetic launch failure", "Failed reopen dismissed choice or hid error")
        check(model.preferences == beforeFailedChoice, "Failed reopen changed session settings or recency")
        failLaunch = false
        model.openProject(currentResume, session: AgentSession())
        try await waitForAction(model)
        check(!launched.last!.0.contains("'resume'") && model.projectToOpen == nil, "New conversation still resumed, or failed launch could not be retried")

        // Claude uses its own flags but shares the same per-open choice and persistence behavior.
        let claudeProject = model.preferences.projects.first { $0.id == saved.id }!
        let beforeClaude = launched.count
        model.reopenProject(claudeProject)
        check(model.projectToOpen == claudeProject && launched.count == beforeClaude, "Claude project launched before choice")
        let claudeModes: [(AgentSession, String)] = [
            (AgentSession(mode: .picker, allProjects: true), "'--' '--resume'"),
            (AgentSession(mode: .latest), "'--' '--continue'"),
            (specific, "'--' '--resume' " + TerminalBridge.shellQuote(specific.sessionID))
        ]
        for (session, suffix) in claudeModes {
            let current = model.preferences.projects.first { $0.id == claudeProject.id }!
            model.reopenProject(current)
            model.openProject(current, session: session)
            try await waitForAction(model)
            check(launched.last!.0.contains("'launch' 'claude'") && launched.last!.0.hasSuffix(suffix), "Claude reopen used wrong flags or lost quoting")
            let restoredClaude = PilotModel(validateTool: { _ in }, storeURL: projectStore, snapshot: { [] })
            let restored = restoredClaude.preferences.projects.first { $0.id == claudeProject.id }!
            check(restored.claudeSession == session && restored.codexSession == nil, "Claude settings did not persist independently")
        }
        model.configureProject(claudeProject, session: AgentSession())
        let currentClaude = model.preferences.projects.first { $0.id == claudeProject.id }!
        model.reopenProject(currentClaude)
        model.openProject(currentClaude, session: currentClaude.session!)
        try await waitForAction(model)
        check(!launched.last!.0.contains("'--resume'") && !launched.last!.0.contains("'--continue'"), "Claude new conversation still resumed")
        let launchCount = launched.count
        for tool in ["codex", "claude"] {
            for invalid in ["", "   ", "--last", "line\nbreak"] {
                model.newTask(tool: tool, directory: root.path, label: "Invalid", session: AgentSession(mode: .specific, sessionID: invalid))
            }
        }
        check(launched.count == launchCount && model.error?.contains("session ID") == true, "Invalid session target was launched")
        // Phone launches use the native project model and quote every path/session argument.
        let phoneDirectory = root.appendingPathComponent("phone-folders")
        for name in ["Visible", ".hidden"] {
            try FileManager.default.createDirectory(at: phoneDirectory.appendingPathComponent(name), withIntermediateDirectories: true)
        }
        try Data("not a folder".utf8).write(to: phoneDirectory.appendingPathComponent("file.txt"))
        let folders = try await model.webRequest("directories", params: ["directory": phoneDirectory.path])
        check((folders["directories"] as? [[String: String]])?.map { $0["name"]! } == ["Visible"], "Phone browser exposed files/hidden directories")
        let phoneLaunch = try await model.webRequest("launch", params: ["tool": "terminal", "directory": directory.path, "name": "Phone ' $(literal)", "pinned": true])
        check(phoneLaunch["opened"] as? Bool == true && phoneLaunch["binding"] as? String == activeTabs.last?.id, "Phone launch lost exact new window binding")
        check(launched.last!.0 == "cd -- " + TerminalBridge.shellQuote(directory.path), "Phone Terminal directory was not safely quoted")
        let phoneProjectID = phoneLaunch["projectID"] as! String
        let phoneProjects = model.preferences.projects.count
        _ = try await model.webRequest("launch", params: ["projectID": phoneProjectID, "tool": "claude", "directory": "/missing", "name": "override", "pinned": false])
        check(launched.last!.1 == "Phone ' $(literal)" && model.preferences.projects.count == phoneProjects && model.preferences.projects.first { $0.id == phoneProjectID }!.pinned, "Saved phone project accepted overrides or duplicated history")
        for tool in ["codex", "claude"] {
            for mode in ["new", "latest", "specific"] {
                _ = try await model.webRequest("launch", params: ["tool": tool, "directory": directory.path, "name": "Phone " + tool, "mode": mode, "sessionID": specific.sessionID])
                let command = launched.last!.0
                let suffix = mode == "new" ? "" : mode == "latest" ? (tool == "codex" ? "'--' 'resume' '--last'" : "'--' '--continue'") : (tool == "codex" ? "'--' 'resume' " : "'--' '--resume' ") + TerminalBridge.shellQuote(specific.sessionID)
                check(mode == "new" ? !command.contains("'--'") : command.hasSuffix(suffix), "Phone agent resume/new mode used wrong arguments")
            }
        }
        let orchestrationID = UUID().uuidString.lowercased()
        let projectsBeforeOrchestration = model.preferences.projects
        let orchestrationResult = try await model.webRequest("launch", params: ["launchID": orchestrationID,
            "tool": "codex", "directory": directory.path, "name": "Specific engineering task", "mode": "specific", "sessionID": specific.sessionID])
        check(orchestrationResult["session"] as? String == orchestrationID && launched.last!.0.contains(TerminalBridge.shellQuote(orchestrationID)), "Orchestrated launch lost durable identity")
        check(launched.last!.0.hasSuffix("'--' 'resume' " + TerminalBridge.shellQuote(specific.sessionID)), "Orchestrated resume lost exact conversation")
        check(model.preferences.projects == projectsBeforeOrchestration, "Work label changed saved project names")
        let phoneCount = launched.count
        for invalid: [String: Any] in [["launchID": "../invalid"], ["tool": "other"], ["directory": "relative"], ["directory": "/missing-phone-directory"], ["directory": phoneDirectory.appendingPathComponent("file.txt").path], ["projectID": "missing"], ["name": "bad\u{001b}name"], ["_deadline": 1.0], ["tool": "codex"], ["tool": "claude", "mode": "picker"], ["tool": "codex", "mode": "specific", "sessionID": "--last"]] {
            let params: [String: Any] = ["tool": "terminal", "directory": directory.path].merging(invalid) { _, new in new }
            do { _ = try await model.webRequest("launch", params: params); check(false, "Invalid phone launch succeeded") }
            catch { check(launched.count == phoneCount, "Invalid phone launch created a window") }
        }
        model.acting = true
        do { _ = try await model.webRequest("launch", params: ["tool": "terminal", "directory": directory.path]); check(false, "Busy phone launch succeeded") }
        catch { check(launched.count == phoneCount, "Busy phone launch created a window") }
        model.acting = false
        let phonePreferences = model.preferences
        failLaunch = true
        do { _ = try await model.webRequest("launch", params: ["tool": "terminal", "directory": directory.path, "name": "Failed phone"]); check(false, "Failed phone launch succeeded") }
        catch { check(model.preferences == phonePreferences && !model.acting, "Failed phone launch changed project history or retained lock") }
        failLaunch = false
        model.query = "no matching native query"
        let phoneState = try await model.webRequest("list", params: [:])
        check((phoneState["projects"] as? [[String: Any]])?.count == model.preferences.projects.count && phoneState["defaultDirectory"] as? String == directory.path, "Native search filtered phone project list or lost default directory")
        model.query = ""

        // Web operations must keep the same session/window binding and attention semantics.
        let webStore = root.appendingPathComponent("web")
        var webTabs = [tab(81), tab(82)]
        var sent: [(String, String)] = []
        var sentTools: [String] = []
        var read: [String] = []
        let webModel = PilotModel(validateTool: { _ in }, storeURL: webStore, snapshot: { webTabs }, readTerminal: { tab in
            read.append(tab.id); return "synthetic terminal"
        }, sendToAgent: { tab, _, tool, text in sent.append((tab.id, text)); sentTools.append(tool) })
        var webEvent = event(81, notice: 100, updated: 100)
        webEvent.tool = "codex"; webEvent.codexThreadID = UUID().uuidString
        try writeEvent(webEvent, store: webStore)
        try writeEvent(event(82, notice: 200, updated: 200), store: webStore)
        let webState = try await webModel.webRequest("list", params: [:])
        check((webState["sessions"] as? [[String: Any]])?.count == 2, "Web did not include both Codex and Claude")
        check(webModel.unreadCount == 2, "Web list cleared notifications")
        let target: [String: Any] = ["session": webEvent.id, "binding": tab(81).id]
        let contents = try await webModel.webRequest("read", params: target)
        check(contents["text"] as? String == "synthetic terminal" && read == [tab(81).id], "Read wrong terminal")
        check(webModel.unreadCount == 2, "Reading terminal cleared notifications")
        var message = target; message["text"] = "first\nsecond"
        _ = try await webModel.webRequest("send", params: message)
        check(sent.count == 1 && sent[0].0 == tab(81).id && sent[0].1 == "first\nsecond", "Send lost target or multiline text")
        check(webModel.unreadCount == 2, "Delivery claimed an AI acknowledgment before hook")
        func rejects(_ method: String, _ params: [String: Any]) async -> Bool {
            do { _ = try await webModel.webRequest(method, params: params); return false } catch { return true }
        }
        var invalid = message; invalid["binding"] = tab(82).id
        let wrongBinding = await rejects("send", invalid)
        check(wrongBinding && sent.count == 1, "Wrong window binding accepted")
        invalid = message; invalid["_deadline"] = Date().timeIntervalSince1970 - 1
        let expired = await rejects("send", invalid)
        check(expired && sent.count == 1, "Expired queued request still delivered input")
        invalid = message; invalid["text"] = "\u{1b}[A"
        let control = await rejects("send", invalid)
        check(control && sent.count == 1, "Terminal control bytes accepted")
        invalid = message; invalid["session"] = "event82"
        let wrongTool = await rejects("send", invalid)
        check(wrongTool, "Claude accepted a Codex window binding")
        webEvent.phase = "waiting"; try writeEvent(webEvent, store: webStore)
        let waiting = await rejects("send", message)
        check(waiting && sent.count == 1, "Sent text into an approval prompt")
        webEvent.phase = "complete"; webEvent.noticeID = "new"; try writeEvent(webEvent, store: webStore)
        let staleAck = await rejects("ack", ["session": webEvent.id, "noticeID": "old"])
        check(staleAck && webModel.unreadCount == 2, "Stale web ack cleared newer notice")
        _ = try await webModel.webRequest("ack", params: ["session": webEvent.id, "noticeID": "new"])
        check(webModel.unreadCount == 1, "Exact web ack did not clear selected notice")
        webTabs = [tab(82)]
        let closedWebWork = await rejects("send", message)
        check(closedWebWork && sent.count == 1, "Closed work still received input")
        var claudeReply: [String: Any] = ["session": "event82", "binding": tab(82).id, "text": "Claude reply"]
        _ = try await webModel.webRequest("send", params: claudeReply)
        check(sent.last?.0 == tab(82).id && sentTools.last == "claude", "Claude reply was not routed to its own agent")
        check(webModel.unreadCount == 1, "Claude send prematurely marked read")
        _ = try await webModel.webRequest("ack", params: ["session": "event82", "noticeID": "notice200.0"])
        check(webModel.unreadCount == 0, "Claude acknowledgment did not synchronize")
        webTabs.append(tab(83))
        let allWork = try await webModel.webRequest("list", params: [:])
        let plain = (allWork["sessions"] as! [[String: Any]]).first { $0["tool"] as? String == "terminal" }!
        check(plain["canSend"] as? Bool == true, "Plain terminal direct input was not advertised")
        _ = try await webModel.webRequest("read", params: ["session": plain["id"]!, "binding": plain["binding"]!])
        claudeReply["session"] = plain["id"]; claudeReply["binding"] = plain["binding"]
        let plainSend = await rejects("send", claudeReply)
        check(plainSend && read.last == tab(83).id, "Plain terminal read/send boundary failed")
        let processes = "10 1 10 20 /bin/zsh\n20 10 20 20 /opt/bin/node\n21 20 20 20 /opt/bin/codex\n"
        check(TerminalBridge.hasForegroundAgent(processes, ancestor: 20, tool: "codex"), "Codex child of managed launcher not recognized")
        check(TerminalBridge.hasForegroundAgent(processes, ancestor: 21, tool: "codex"), "Direct managed Codex not recognized")
        check(!TerminalBridge.hasForegroundAgent(processes, ancestor: 99, tool: "codex"), "Unrelated Codex process accepted")
        check(!TerminalBridge.hasForegroundAgent(processes.replacingOccurrences(of: "20 20 /opt/bin/codex", with: "20 10 /opt/bin/codex"), ancestor: 20, tool: "codex"), "Background Codex accepted")
        check(!TerminalBridge.hasForegroundAgent("10 1 10 10 /bin/zsh", ancestor: 10, tool: "codex"), "Shell accepted as Codex")
        let claudeProcesses = processes.replacingOccurrences(of: "/opt/bin/codex", with: "/opt/bin/claude")
        check(TerminalBridge.hasForegroundAgent(claudeProcesses, ancestor: 20, tool: "claude"), "Claude foreground process not recognized")
        check(!TerminalBridge.hasForegroundAgent(claudeProcesses, ancestor: 20, tool: "codex"), "Wrong agent executable accepted")
        let versioned = claudeProcesses.replacingOccurrences(of: "/opt/bin/claude", with: "/opt/claude/versions/2.1.301")
        check(TerminalBridge.hasForegroundAgent(versioned, ancestor: 20, tool: "claude", executable: "/opt/claude/versions/2.1.301"), "Native Claude versioned executable not recognized")
        // Migration stops only the exact bound Codex and waits for stop success before resuming.
        let migrationStore = root.appendingPathComponent("migration")
        var migrationOrder: [String] = [], failStop = false
        var migrationEvent = event(91, notice: 0, updated: 10)
        migrationEvent.tool = "codex"; migrationEvent.codexThreadID = UUID().uuidString
        migrationEvent.codexHome = root.appendingPathComponent("custom codex home").path
        let migrationModel = PilotModel(validateTool: { _ in }, storeURL: migrationStore, snapshot: { [tab(91)] }, launch: { command, _ in
            migrationOrder.append("launch")
            check(command.contains("resume") && command.contains(migrationEvent.codexThreadID!), "Migration lost conversation identity")
            check(command.contains("--codex-home") && command.contains("custom codex home"), "Migration changed Codex home")
            return tab(92)
        }, stopForResume: { target, pid in
            check(target.id == tab(91).id && pid == migrationEvent.pid, "Migration stopped another process")
            migrationOrder.append("stop")
            if failStop { throw PilotError(message: "Still running") }
        })
        try writeEvent(migrationEvent, store: migrationStore)
        let migrationTarget: [String: Any] = ["session":migrationEvent.id,"binding":tab(91).id]
        let migration = try await migrationModel.webRequest("approvalUpgrade", params:migrationTarget)
        check(migration["opened"] as? Bool == true && migrationOrder == ["stop","launch"], "Migration launch preceded stop")
        migrationOrder = []; failStop = true
        do { _ = try await migrationModel.webRequest("approvalUpgrade", params:migrationTarget); check(false,"Failed stop allowed migration") } catch {}
        check(migrationOrder == ["stop"], "Failed stop launched a duplicate writer")
        migrationOrder = []
        var expiredMigration = migrationTarget; expiredMigration["_deadline"] = Date().timeIntervalSince1970 - 1
        do { _ = try await migrationModel.webRequest("approvalUpgrade", params:expiredMigration); check(false,"Expired migration allowed") } catch {}
        check(migrationOrder.isEmpty,"Expired migration stopped a process")
        migrationEvent.codexSocket = "/tmp/managed/socket"; try writeEvent(migrationEvent, store:migrationStore)
        do { _ = try await migrationModel.webRequest("approvalUpgrade", params:migrationTarget); check(false,"Managed work migrated again") } catch {}
        check(migrationOrder.isEmpty,"Already managed work stopped")
        let closeStore = root.appendingPathComponent("close")
        var closeTabs = [tab(101),tab(102)], closedBindings: [String] = []
        let closeModel = PilotModel(validateTool: { _ in }, storeURL:closeStore, snapshot:{closeTabs}, closeTerminal:{ targets in
            closedBindings = targets.map(\.id);closeTabs.removeAll { closedBindings.contains($0.id) }
        })
        let closeState = try await closeModel.webRequest("list",params:[:])
        let closeWork = (closeState["sessions"] as! [[String:Any]]).first { $0["binding"] as? String == tab(101).id }!
        let closeTarget:[String:Any] = ["session":closeWork["id"]!,"binding":tab(101).id]
        let preview = try await closeModel.webRequest("closePreview",params:closeTarget)
        check(preview["bindings"] as? [String] == [tab(101).id] && closedBindings.isEmpty,"Close preview changed a window")
        var closeRequest = closeTarget;closeRequest["bindings"] = [tab(102).id]
        do { _ = try await closeModel.webRequest("close",params:closeRequest);check(false,"Unreviewed window accepted") } catch {}
        check(closedBindings.isEmpty,"Unreviewed window stopped")
        closeRequest["bindings"] = [tab(101).id];closeRequest["_deadline"] = 1.0
        do { _ = try await closeModel.webRequest("close",params:closeRequest);check(false,"Expired close accepted") } catch {}
        check(closedBindings.isEmpty,"Expired close stopped a process")
        closeRequest.removeValue(forKey:"_deadline")
        let closeResult = try await closeModel.webRequest("close",params:closeRequest)
        check(closeResult["closed"] as? Bool == true && closedBindings == [tab(101).id],"Close targeted wrong terminal")
        check(closeTabs.map(\.id) == [tab(102).id],"Close removed unrelated work")
        do { _ = try await closeModel.webRequest("close",params:closeRequest);check(false,"Stale closed binding accepted") } catch {}
        let closeJobs = try TerminalBridge.closeProcesses("100 100 501\n101 100 501",uid:501)
        let loginJobs = try TerminalBridge.closeProcesses("99 99 0\n100 100 501",uid:501,executable: { $0 == 99 ? "/usr/bin/login" : nil })
        check(loginJobs.count == 1 && loginJobs.first?.0 == 100, "Root login supervisor is left to clean up its shell")
        do { _ = try TerminalBridge.closeProcesses("99 99 0",uid:501,executable: { _ in "/tmp/login" });check(false,"Unverified root supervisor accepted") } catch {}
        check(closeJobs.count == 2,"TTY process parser lost jobs")
        do { _ = try TerminalBridge.closeProcesses("100 100 0",uid:501);check(false,"Foreign user process accepted") } catch {}
        // Phone creation is one literal child, never shell execution or recursive mkdir.
        let folderParent = root.appendingPathComponent("phone-create")
        try FileManager.default.createDirectory(at: folderParent, withIntermediateDirectories: true)
        let folderModel = PilotModel(validateTool: { _ in }, storeURL: root.appendingPathComponent("folder-store"), snapshot: { [] })
        let literalName = "新專案 ' $(literal)"
        let createdFolder = try await folderModel.webRequest("createDirectory", params: ["parent": folderParent.path, "name": literalName])
        check(createdFolder["created"] as? Bool == true && createdFolder["directory"] as? String == folderParent.appendingPathComponent(literalName).path,
              "Folder creation changed its literal name")
        check(FileManager.default.fileExists(atPath: folderParent.appendingPathComponent(literalName).path), "Folder was not created")
        for invalidName in ["", "..", ".", "../escape", "one/two", "one\\two", "bad\nname", String(repeating: "中", count: 86)] {
            do { _ = try await folderModel.webRequest("createDirectory", params: ["parent": folderParent.path, "name": invalidName]); check(false, "Invalid folder name accepted") } catch { checks += 1 }
        }
        let existingFile = folderParent.appendingPathComponent("existing-file")
        try Data("keep".utf8).write(to: existingFile)
        let existingLink = folderParent.appendingPathComponent("existing-link")
        try FileManager.default.createSymbolicLink(at: existingLink, withDestinationURL: folderParent.appendingPathComponent(literalName))
        for name in [literalName, "existing-file", "existing-link"] {
            do { _ = try await folderModel.webRequest("createDirectory", params: ["parent": folderParent.path, "name": name]); check(false, "Existing entry was reused") } catch { checks += 1 }
        }
        let keptFile = try String(contentsOf: existingFile, encoding: .utf8)
        check(keptFile == "keep", "Creation overwrote existing content")
        for invalidParent in [folderParent.appendingPathComponent("missing").path, existingFile.path, "relative"] {
            do { _ = try await folderModel.webRequest("createDirectory", params: ["parent": invalidParent, "name": "new"]); check(false, "Invalid parent was accepted") } catch { checks += 1 }
        }
        do { _ = try await folderModel.webRequest("createDirectory", params: ["parent": folderParent.path, "name": "expired", "_deadline": 1.0]); check(false, "Expired request created a folder") } catch {}
        check(!FileManager.default.fileExists(atPath: folderParent.appendingPathComponent("expired").path), "Expired creation had side effects")

        // Workspace trust is a separate, explicit decision before Claude hooks start.
        let trustStore = root.appendingPathComponent("trust"), trustTab = tab(110)
        var trustEvent = event(110, notice: 0, updated: 100, phase: "connected")
        let trustScreen = """
        Accessing workspace:
          \(root.path)
        Quick safety check: Is this a project you created or one you trust? (Like your
        own code, a well-known open source project, or work from your team). If not,
        take a moment to review what's in this folder first.
        Claude Code'll be able to read, edit, and execute files here.
        Security guide
        ❯ No, exit
          Yes, I trust this folder
        Enter to confirm · Esc to cancel
        """
        let parsedTrust = ClaudeTrustPrompt.parse(trustScreen, directory: root.path)!
        let yesKeys = try parsedTrust.keys(action: "accept"), noKeys = try parsedTrust.keys(action: "decline")
        check(yesKeys == "\u{1b}[B" && noKeys.isEmpty, "Current default No requires Down before accepting")
        let selectedYes = trustScreen.replacingOccurrences(of: "❯ No, exit", with: "No, exit")
            .replacingOccurrences(of: "  Yes, I trust", with: "❯ Yes, I trust")
        let selectedPrompt = ClaudeTrustPrompt.parse(selectedYes, directory: root.path)!
        let declineKeys = try selectedPrompt.keys(action: "decline")
        check(declineKeys == "\u{1b}[A", "Decline must move back from Yes")
        check(ClaudeTrustPrompt.parse(trustScreen, directory: root.path + "/different") == nil, "Wrong directory was trusted")
        check(ClaudeTrustPrompt.parse(trustScreen + "\nClaude is now working", directory: root.path) == nil, "Scrollback prompt was treated as current")
        check(ClaudeTrustPrompt.parse(trustScreen.replacingOccurrences(of: "❯", with: ""), directory: root.path) == nil, "Unknown selection was guessed")
        check(ClaudeTrustPrompt.parse(trustScreen.replacingOccurrences(of: "No, exit", with: "Always allow commands"), directory: root.path) == nil, "Unrecognized prompt was accepted")
        var trustVisible = true, trustAnswers: [(String, String)] = [], failTrustDelivery = false
        func trustModel() -> PilotModel {
            PilotModel(validateTool: { _ in }, storeURL: trustStore, snapshot: { [trustTab] },
                inspectClaudeTrust: { _, _, _ in trustVisible ? parsedTrust : nil },
                answerClaudeTrust: { tab, _, path, action in
                    check(path == root.path, "Trust used a client-provided path")
                    trustAnswers.append((tab.id, action))
                    if failTrustDelivery { throw PilotError(message: "Ambiguous transport failure") }
                })
        }
        try writeEvent(trustEvent, store: trustStore)
        let trust = trustModel()
        let trustState = try await trust.webRequest("list", params: [:])
        let trustSession = (trustState["sessions"] as! [[String: Any]])[0]
        let trustCard = ((trustSession["runtime"] as! [String: Any])["approvals"] as! [[String: Any]])[0]
        check(trustCard["kind"] as? String == "workspace_trust" && trustAnswers.isEmpty, "Reading auto-accepted trust")
        check(trustSession["canSend"] as? Bool == false && trustSession["phase"] as? String == "waiting", "Trust wait allowed normal replies")
        let trustNoticeID = trustCard["id"] as! String
        check(trust.rows[0].noticeID == trustNoticeID && trust.rows[0].unread,
              "Claude trust must use the same notice on Mac and phone")
        trust.markRead(trust.rows[0])
        let readTrust = try await trust.webRequest("list", params: [:])
        check((readTrust["sessions"] as! [[String: Any]])[0]["unread"] as? Bool == false && trustAnswers.isEmpty,
              "Mac acknowledgment did not clear phone trust notice, or answered the prompt")
        let decision: [String: Any] = ["session": trustEvent.id, "binding": trustTab.id,
            "approval": trustCard["id"]!, "requestID": "trust-1", "action": "accept"]
        for change: [String: Any] in [["binding": tab(111).id], ["approval": "stale"], ["action": "always"], ["_deadline": 1.0]] {
            do { _ = try await trust.webRequest("claudeTrustDecision", params: decision.merging(change) { _, value in value }); check(false, "Invalid trust request accepted") } catch {}
            check(trustAnswers.isEmpty, "Invalid trust request injected input")
        }
        do { _ = try await trust.webRequest("send", params: ["session": trustEvent.id, "binding": trustTab.id, "text": "yes"]); check(false, "Text sent to trust dialog") } catch {}
        trustVisible = false
        do { _ = try await trust.webRequest("claudeTrustDecision", params: decision); check(false, "Resolved prompt received input") } catch {}
        check(trustAnswers.isEmpty, "A stale card answered another screen")
        trustVisible = true
        _ = try await trust.webRequest("claudeTrustDecision", params: decision)
        _ = try await trust.webRequest("claudeTrustDecision", params: decision)
        _ = try await trustModel().webRequest("claudeTrustDecision", params: decision)
        check(trustAnswers.count == 1 && trustAnswers[0].0 == trustTab.id && trustAnswers[0].1 == "accept", "Trust retry or restart sent duplicate keys")
        var secondTap = decision; secondTap["requestID"] = "new-tap"
        do { _ = try await trust.webRequest("claudeTrustDecision", params: secondTap); check(false, "Second request repeated same approval") } catch {}
        check(trustAnswers.count == 1, "Another request ID repeated approval")
        trustEvent.id = "fresh-trust"; trustEvent.createdAt += 1; try writeEvent(trustEvent, store: trustStore)
        let fresh = trustModel()
        let freshID = ClaudeTrustPrompt.identity(event: trustEvent, tab: trustTab)
        let freshDecision: [String: Any] = ["session": trustEvent.id, "binding": trustTab.id, "approval": freshID,
                                           "requestID": "trust-2", "action": "decline"]
        failTrustDelivery = true
        do { _ = try await fresh.webRequest("claudeTrustDecision", params: freshDecision); check(false, "Ambiguous failure was reported as success") } catch {}
        let uncertainTrust = try await trustModel().webRequest("claudeTrustDecision", params: freshDecision)
        check(uncertainTrust["uncertain"] as? Bool == true && trustAnswers.count == 2, "Restart retried uncertain trust input")
        trustEvent.claudeSessionID = UUID().uuidString; try writeEvent(trustEvent, store: trustStore)
        let started = try await trustModel().webRequest("list", params: [:])
        let startedSession = (started["sessions"] as! [[String: Any]]).first { $0["id"] as? String == trustEvent.id }!
        check(startedSession["runtime"] == nil, "An established conversation could spoof startup trust")
        // Plain Terminal input is a separate, explicit user action with durable receipts.
        let inputStore = root.appendingPathComponent("terminal-input"), inputTab = tab(119)
        var inputTabs = [inputTab], inputs: [(String, String)] = [], inputFailure = false
        func inputModel() -> PilotModel {
            PilotModel(validateTool: { _ in }, storeURL: inputStore, snapshot: { inputTabs },
                sendToAgent: { _, _, _, _ in check(false, "Plain input used agent path") },
                sendToTerminal: { target, text in
                    inputs.append((target.id, text))
                    if inputFailure { throw PilotError(message: "Transport lost") }
                })
        }
        let inputModelOne = inputModel()
        let inputState = try await inputModelOne.webRequest("list", params: [:])
        let inputSession = (inputState["sessions"] as! [[String: Any]])[0]
        check(inputSession["tool"] as? String == "terminal" && inputSession["canSend"] as? Bool == true, "Plain terminal input disabled")
        let literalInput = "printf '%s\\n' \"literal `text` $(text)\"\nsecond line"
        let inputRequest: [String: Any] = ["session": inputSession["id"]!, "binding": inputTab.id,
                                         "requestID": "input-1", "text": literalInput]
        for change: [String: Any] in [["binding": tab(118).id], ["requestID": ""], ["text": "\u{1b}[A"],
            ["text": "\u{3}"], ["text": String(repeating: "x", count: 8001)], ["_deadline": 1.0], ["command": "extra"]] {
            do { _ = try await inputModelOne.webRequest("terminalInput", params: inputRequest.merging(change) { _, value in value }); check(false, "Invalid terminal input accepted") } catch {}
        }
        check(inputs.isEmpty, "Invalid terminal input reached a process")
        _ = try await inputModelOne.webRequest("terminalInput", params: inputRequest)
        _ = try await inputModelOne.webRequest("terminalInput", params: inputRequest)
        _ = try await inputModel().webRequest("terminalInput", params: inputRequest)
        check(inputs.count == 1 && inputs[0].0 == inputTab.id && inputs[0].1 == literalInput, "Terminal retry/restart duplicated or changed input")
        do { _ = try await inputModelOne.webRequest("terminalInput", params: inputRequest.merging(["text": "other"]) { _, value in value }); check(false, "Changed request reused terminal receipt") } catch {}
        let journalData = try String(contentsOf: inputStore.appendingPathComponent("terminal-input-receipts.json"), encoding: .utf8)
        check(!journalData.contains("printf") && !journalData.contains("second line"), "Journal persisted command content")
        var enterRequest = inputRequest; enterRequest["requestID"] = "enter"; enterRequest["text"] = ""
        _ = try await inputModelOne.webRequest("terminalInput", params: enterRequest)
        check(inputs.last?.1 == "", "Explicit Enter was rejected or changed")
        inputFailure = true
        var uncertainInput = inputRequest; uncertainInput["requestID"] = "input-uncertain"
        do { _ = try await inputModelOne.webRequest("terminalInput", params: uncertainInput); check(false, "Ambiguous terminal result reported success") } catch {}
        let inputRetry = try await inputModel().webRequest("terminalInput", params: uncertainInput)
        check(inputRetry["uncertain"] as? Bool == true && inputs.count == 3, "Ambiguous terminal input repeated after restart")
        inputTabs = []
        do { _ = try await inputModel().webRequest("terminalInput", params: inputRequest); check(false, "Closed terminal received input") } catch {}
        inputTabs = [inputTab]
        try writeEvent(event(119, notice: 0, updated: 0, phase: "connected"), store: inputStore)
        do { _ = try await inputModel().webRequest("terminalInput", params: inputRequest); check(false, "Agent task accepted raw terminal input") } catch {}
        check(inputs.count == 3, "Closed/replaced target got input")

        // Pre-thread TUI menus do not use Codex's thread approval channel.
        let startupStore = root.appendingPathComponent("startup"), startupTab = tab(120)
        var startupEvent = event(120, notice: 0, updated: 100, phase: "connected")
        startupEvent.tool = "codex"; startupEvent.lifecycle = "pending"
        let hooksScreen = """
        Hooks need review
          7 hooks are new or changed.
          Hooks can run outside the sandbox after you trust them.
        › 1. Review hooks
          2. Trust all and continue
          3. Continue without trusting (hooks won't run)
          enter confirm · esc skip
        """
        let hookPrompt = StartupPrompt.parse(hooksScreen)!
        check(hookPrompt.actions == ["choice_2", "choice_3"], "Hook review should be shown inline; no implicit trust")
        let trustHookKeys = try hookPrompt.keys(action: "choice_2")
        let skipHookKeys = try hookPrompt.keys(action: "choice_3")
        check(trustHookKeys == "\u{1b}[B" && skipHookKeys == "\u{1b}[B\u{1b}[B", "Hook selection sent wrong navigation")
        let movedScreen = hooksScreen.replacingOccurrences(of: "› 1.", with: "1.").replacingOccurrences(of: "  3.", with: "› 3.")
        let movedPrompt = StartupPrompt.parse(movedScreen)!
        let movedKeys = try movedPrompt.keys(action: "choice_2")
        check(movedKeys == "\u{1b}[A", "Selection must be recomputed from the current screen")
        check(hookPrompt.identity(event: startupEvent, tab: startupTab) == movedPrompt.identity(event: startupEvent, tab: startupTab), "Selection cursor changed approval identity")
        for screen in ["Trust this folder?\n /project\n› 1. Yes, trust this folder\n2. No, exit\nenter confirm · esc skip",
                       "Update available\n› 1. Update now (runs `npm install`)\n2. Skip until next version\nenter confirm · esc skip"] {
            let menu = StartupPrompt.parse(screen)!
            check(menu.actions == ["choice_1", "choice_2"] && !menu.hooks, "Other startup menus were not exposed")
        }
        let wrapped = hooksScreen.replacingOccurrences(of: "Continue without trusting (hooks won't run)", with: "Continue without trusting (hooks\nwon't run)")
        check(StartupPrompt.parse(wrapped)!.text == hookPrompt.text, "Narrow window wrapping changed the decision")
        check(StartupPrompt.parse(hooksScreen + "\nAgent is working") == nil, "Scrollback was treated as the current menu")
        for screen in [hooksScreen.replacingOccurrences(of: "›", with: ""),
                       hooksScreen.replacingOccurrences(of: "2.", with: "4."),
                       hooksScreen.replacingOccurrences(of: "  2.", with: "› 2.")] {
            check(StartupPrompt.parse(screen)!.actions.isEmpty, "Unknown selection/order must be read-only")
        }
        var startupVisible: StartupPrompt? = hookPrompt
        var startupAnswers: [(String, String)] = [], failStartup = false
        func startupModel() -> PilotModel {
            PilotModel(validateTool: { _ in }, storeURL: startupStore, snapshot: { [startupTab] },
                sendToAgent: { _, _, _, _ in check(false, "Normal text reached startup menu") },
                inspectStartup: { _, _ in startupVisible },
                answerStartup: { target, _, expected, action in
                    check(expected == startupVisible?.text, "Wrong startup snapshot used")
                    startupAnswers.append((target.id, action))
                    if failStartup { throw PilotError(message: "Ambiguous startup transport failure") }
                })
        }
        try writeEvent(startupEvent, store: startupStore)
        let startup = startupModel()
        var startupBadge = -1
        startup.onCount = { startupBadge = $0 }
        await startup.refresh()
        check(startup.rows[0].noticeID.hasPrefix("startup:") && startup.rows[0].phase == "waiting" && startupBadge == 1,
              "Mac must detect startup attention without a phone list request")
        let startupState = try await startup.webRequest("list", params: [:])
        let startupSession = (startupState["sessions"] as! [[String: Any]])[0]
        let startupCard = ((startupSession["runtime"] as! [String: Any])["approvals"] as! [[String: Any]])[0]
        check(startupSession["canSend"] as? Bool == false && startupSession["unread"] as? Bool == true && startupAnswers.isEmpty, "Reading startup must expose notice without answering")
        let startupID = startupCard["id"] as! String
        _ = try await startup.webRequest("ack", params: ["session": startupEvent.id, "noticeID": startupID])
        check(!startup.rows[0].unread && startupBadge == 0, "Phone ack left the Mac row or Dock unread")
        let acknowledgedStartup = try await startup.webRequest("list", params: [:])
        check((acknowledgedStartup["sessions"] as! [[String: Any]])[0]["unread"] as? Bool == false && startupAnswers.isEmpty, "Reading must clear notice, not trust hooks")
        do { _ = try await startup.webRequest("send", params: ["session": startupEvent.id, "binding": startupTab.id, "text": "2"]); check(false, "Chat could answer startup") } catch {}
        let startupDecision: [String: Any] = ["session": startupEvent.id, "binding": startupTab.id,
            "approval": startupID, "requestID": "startup-1", "action": "choice_2"]
        for change: [String: Any] in [["binding": tab(121).id], ["approval": "startup:stale"], ["action": "choice_99"], ["_deadline": 1.0], ["keys": "yes"]] {
            do { _ = try await startup.webRequest("startupDecision", params: startupDecision.merging(change) { _, value in value }); check(false, "Invalid startup accepted") } catch {}
        }
        check(startupAnswers.isEmpty, "Invalid decision sent keys")
        let oldStartupRow = startup.rows[0]
        startupVisible = StartupPrompt.parse(hooksScreen.replacingOccurrences(of: "7 hooks", with: "8 hooks"))!
        await startup.refresh()
        check(startup.rows[0].unread && startupBadge == 1, "A changed prompt must alert both devices again")
        check(!startup.markRead(oldStartupRow) && startup.rows[0].unread, "An old Mac click cleared a newer prompt")
        do { _ = try await startup.webRequest("ack", params: ["session": startupEvent.id, "noticeID": startupID]); check(false, "Old phone ack cleared a new prompt") } catch {}
        startup.markRead(startup.rows[0])
        let macReadStartup = try await startup.webRequest("list", params: [:])
        check((macReadStartup["sessions"] as! [[String: Any]])[0]["unread"] as? Bool == false && startupBadge == 0,
              "Mac startup acknowledgment did not reach the phone")
        do { _ = try await startup.webRequest("startupDecision", params: startupDecision); check(false, "Changed hooks accepted") } catch {}
        startupVisible = movedPrompt
        _ = try await startup.webRequest("startupDecision", params: startupDecision)
        _ = try await startup.webRequest("startupDecision", params: startupDecision)
        _ = try await startupModel().webRequest("startupDecision", params: startupDecision)
        check(startupAnswers.count == 1 && startupAnswers[0].0 == startupTab.id, "Retry/restart repeated startup input")
        var anotherTap = startupDecision; anotherTap["requestID"] = "startup-new-tap"
        do { _ = try await startup.webRequest("startupDecision", params: anotherTap); check(false, "Duplicate tap accepted") } catch {}
        startupVisible = StartupPrompt.parse(hooksScreen.replacingOccurrences(of: "7 hooks", with: "8 hooks"))!
        var newDecision = startupDecision
        newDecision["approval"] = startupVisible!.identity(event: startupEvent, tab: startupTab)
        newDecision["requestID"] = "startup-2"; newDecision["action"] = "choice_3"
        failStartup = true
        do { _ = try await startup.webRequest("startupDecision", params: newDecision); check(false, "Ambiguous delivery reported success") } catch {}
        let startupRetry = try await startupModel().webRequest("startupDecision", params: newDecision)
        check(startupRetry["uncertain"] as? Bool == true && startupAnswers.count == 2, "Ambiguous startup was replayed")
        startupEvent.codexThreadID = UUID().uuidString; try writeEvent(startupEvent, store: startupStore)
        let startedCodex = try await startupModel().webRequest("list", params: [:])
        check((startedCodex["sessions"] as! [[String: Any]])[0]["runtime"] == nil, "Established conversation could spoof startup menu")
        var brainLaunches: [(String, String)] = []
        let brainModel = PilotModel(validateTool: { _ in }, storeURL: root.appendingPathComponent("brain-launch"), snapshot: { [] }, launch: { command, title in
            brainLaunches.append((command, title)); return tab(901)
        })
        let brainIdentity = UUID().uuidString.lowercased()
        for params: [String: Any] in [
            ["launchID": "../../other"], ["launchID": brainIdentity, "threadID": "--latest"],
            ["launchID": brainIdentity, "command": "unexpected"], ["launchID": brainIdentity, "_deadline": 1.0]
        ] {
            do { _ = try await brainModel.webRequest("brainOpen", params: params); check(false, "Invalid Brain launch was accepted") }
            catch {}
        }
        check(brainLaunches.isEmpty, "Invalid Brain open created a Terminal")
        if FileManager.default.fileExists(atPath: FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("brain").path) {
            let priorDirectory = brainModel.preferences.lastDirectory
            let brainThread = UUID().uuidString.lowercased()
            _ = try await brainModel.webRequest("brainOpen", params: ["launchID": brainIdentity, "threadID": brainThread])
            check(brainLaunches.count == 1 && brainLaunches[0].1 == "Brain · 個人助理", "Dedicated Brain title missing")
            check(brainLaunches[0].0.contains(TerminalBridge.shellQuote(brainIdentity)) && brainLaunches[0].0.contains(TerminalBridge.shellQuote(brainThread)), "Brain launch lost exact session/thread")
            check(!brainLaunches[0].0.contains("--last") && !brainLaunches[0].0.contains("--dangerously"), "Brain changed resume or permission policy")
            check(brainModel.preferences.lastDirectory == priorDirectory && brainModel.preferences.projects.isEmpty, "Brain open changed project history/default")
        }
        print("Passed \(checks) model checks, including web targeting, delivery guards and unread races.")
    }
}
