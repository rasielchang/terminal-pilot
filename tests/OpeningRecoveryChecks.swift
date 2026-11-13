import Foundation
import Darwin

@MainActor func openingRecoveryChecks(root: URL) async throws -> Int {
    var checks = 0
    func check(_ condition: @autoclosure () -> Bool, _ message: String) {
        precondition(condition(), message); checks += 1
    }
    func tab(_ index: Int) -> TerminalTab {
        TerminalTab(tty: "/dev/test\(index)", windowID: index, title: "Synthetic opening",
                    minimized: false, bounds: [], selected: true, terminalPID: 123)
    }
    func event(_ index: Int, notice: Double, updated: Double, phase: String) -> AgentEvent {
        AgentEvent(id: "event\(index)", tty: tab(index).tty, tool: "codex", cwd: root.path,
                   label: "Synthetic opening", pid: Int(getpid()), phase: phase, createdAt: Double(index),
                   updatedAt: updated, noticeID: "", noticeAt: notice, noticePhase: phase)
    }
    func writeEvent(_ value: AgentEvent, store: URL) throws {
        let sessions = store.appendingPathComponent("sessions")
        try FileManager.default.createDirectory(at: sessions, withIntermediateDirectories: true)
        try JSONEncoder().encode(value).write(to: sessions.appendingPathComponent("\(value.id).json"))
    }
    // An opening can lack a thread yet retain an exact native binding.
    // Recovery must prove absence against that persisted window generation.
    let orphanStore = root.appendingPathComponent("orphan-opening")
    let orphanID = UUID().uuidString
    var orphanEvent = event(951, notice: 0, updated: 1, phase: "running")
    orphanEvent.id = orphanID; orphanEvent.tool = "codex"; orphanEvent.codexThreadID = nil
    try writeEvent(orphanEvent, store: orphanStore)
    var orphanTabs = [tab(951)], orphanPartial = false, orphanProbeFails = false
    let orphanModel = PilotModel(agentIdentity: { _,_,_ in "agent" }, terminalBirth: { _ in "birth" },
        storeURL: orphanStore, snapshot: {
            if orphanPartial { throw PartialTerminalSnapshot(tabs: [], unidentifiedWindows: 1) }
            return orphanTabs
        }, probeWindow: { _ in
            if orphanProbeFails { throw PilotError(message: "Unavailable") }
            return orphanTabs
        })
    let orphanQuery: [String:Any] = ["launchID": orphanID, "tool": "codex", "directory": root.path]
    let liveOpening = try await orphanModel.webRequest("openingStatus", params: orphanQuery)
    check(liveOpening["reliable"] as? Bool == true && liveOpening["absent"] as? Bool == false,
          "Live unbound opening was declared absent")
    orphanTabs = []; orphanPartial = true; orphanProbeFails = true
    let unknownOpening = try await orphanModel.webRequest("openingStatus", params: orphanQuery)
    check(unknownOpening["reliable"] as? Bool == false, "Partial inventory became closure proof")
    orphanProbeFails = false
    let closedOpening = try await orphanModel.webRequest("openingStatus", params: orphanQuery)
    check(closedOpening["reliable"] as? Bool == true && closedOpening["absent"] as? Bool == true &&
          closedOpening["evidence"] as? String == "exact_window_tabs" &&
          closedOpening["generation"] as? String == tab(951).id + "@birth", "Exact orphan closure evidence lost")
    let orphanRestart = PilotModel(terminalBirth: { _ in "birth" }, storeURL: orphanStore, snapshot: { [] })
    let restartOpening = try await orphanRestart.webRequest("openingStatus", params: orphanQuery)
    check(restartOpening["absent"] as? Bool == true && restartOpening["launchID"] as? String == orphanID,
          "Restart lost persisted opening evidence")
    var wrongOpening = orphanQuery; wrongOpening["directory"] = "/different"
    let wrongOpeningProof = try await orphanRestart.webRequest("openingStatus", params: wrongOpening)
    check(wrongOpeningProof["reliable"] as? Bool == false, "Recovery accepted another work directory")
    wrongOpening = orphanQuery; wrongOpening["launchID"] = UUID().uuidString
    let missingOpeningProof = try await orphanRestart.webRequest("openingStatus", params: wrongOpening)
    check(missingOpeningProof["reliable"] as? Bool == false, "Missing launch record became closure proof")

    return checks
}
