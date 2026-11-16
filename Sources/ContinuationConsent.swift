import AppKit

/// A paired API client can request this dialog, but cannot approve its scope.
/// The private native reply records an explicit local UI decision, not OS identity.
@MainActor enum ContinuationConsent {
    static var busy = false
    static let method = "native_scope_confirmation"

    static func scope(_ params: [String: Any], now: TimeInterval) throws -> (String, String, String) {
        guard let challenge = params["challenge"] as? String, !challenge.isEmpty, challenge.count <= 100,
              let digest = params["digest"] as? String, digest.count == 64,
              let text = params["text"] as? String, !text.isEmpty, text.count <= 8000,
              let deadline = params["_deadline"] as? Double, now < deadline else {
            throw PilotError(message: "確認內容無效或已逾時；沒有建立接續授權。")
        }
        return (challenge, digest, text)
    }

    static func responseReceipt(_ response: NSApplication.ModalResponse, params: [String: Any],
                                now: TimeInterval = Date().timeIntervalSince1970) throws -> [String: Any] {
        guard response == .alertFirstButtonReturn else { throw PilotError(message: "已取消這次接續。") }
        let (challenge, digest, _) = try scope(params, now: now)
        return ["approved": true, "method": method, "challenge": challenge, "digest": digest]
    }

    static func confirm(_ params: [String: Any]) async throws -> [String: Any] {
        guard !busy else { throw PilotError(message: "已有接續確認視窗，請先完成。") }
        let (_, _, text) = try scope(params, now: Date().timeIntervalSince1970)
        busy = true
        defer { busy = false }
        let panel = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 640, height: 120),
                             styleMask: [.titled], backing: .buffered, defer: false)
        panel.title = "Brain 接續確認"
        panel.center(); panel.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        defer { panel.orderOut(nil) }
        let alert = NSAlert()
        alert.messageText = "同意這次 Brain 接續？"
        alert.informativeText = "請核對下列訊息和動作，再決定是否同意。不需要 Touch ID 或系統密碼；只授權這次列出的範圍，其他正式核准照常。\n\n" + text
        alert.addButton(withTitle: "同意這次接續")
        alert.addButton(withTitle: "取消")
        let response: NSApplication.ModalResponse = await withCheckedContinuation { continuation in
            alert.beginSheetModal(for: panel) { continuation.resume(returning: $0) }
        }
        return try responseReceipt(response, params: params)
    }
}
