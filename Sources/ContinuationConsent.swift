import AppKit
import LocalAuthentication

/// A transcript user role and the shared web token are not human identity.
/// Only this private native RPC returns a device-owner-bound scope receipt.
@MainActor enum ContinuationConsent {
    static var busy = false
    static func confirm(_ params: [String: Any]) async throws -> [String: Any] {
        guard !busy, let challenge = params["challenge"] as? String, challenge.count <= 100,
              let digest = params["digest"] as? String, digest.count == 64,
              let text = params["text"] as? String, text.count <= 8000 else {
            throw PilotError(message: "本人確認資料無效或已有確認視窗。")
        }
        busy = true
        defer { busy = false }
        let context = LAContext()
        var error: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else {
            throw PilotError(message: "此 Mac 無法驗證本人；沒有建立接續授權。")
        }
        let panel = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 640, height: 120),
                             styleMask: [.titled], backing: .buffered, defer: false)
        panel.title = "Brain 接續授權"
        panel.center(); panel.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        defer { panel.orderOut(nil); context.invalidate() }
        let alert = NSAlert()
        alert.messageText = "確認這次 Brain 接續"
        alert.informativeText = "原對話來源尚不能證明真人輸入。以下確認會建立新的有限授權；不授權合併、部署、刪除或代答核准。\n\n" + text
        alert.addButton(withTitle: "確認範圍並驗證本人")
        alert.addButton(withTitle: "取消")
        let response: NSApplication.ModalResponse = await withCheckedContinuation { continuation in
            alert.beginSheetModal(for: panel) { continuation.resume(returning: $0) }
        }
        guard response == .alertFirstButtonReturn else { throw PilotError(message: "本人取消接續授權。") }
        let verified = await withCheckedContinuation { continuation in
            context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "授權剛才顯示的 Brain 接續範圍") { ok, _ in
                continuation.resume(returning: ok)
            }
        }
        guard verified, let deadline = params["_deadline"] as? Double, Date().timeIntervalSince1970 < deadline else {
            throw PilotError(message: "本人驗證未完成或已逾時；沒有授權。")
        }
        return ["approved": true, "method": "macos_device_owner", "challenge": challenge, "digest": digest]
    }
}
