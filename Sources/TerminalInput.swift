import Foundation
import CryptoKit

struct TerminalInputReceipt: Codable {
    let fingerprint: String
    var sent: Bool
}

/// Persist only identity hashes/results. Commands and passwords stay out of the journal.
final class TerminalInputJournal {
    private let file: URL
    private var receipts: [String: TerminalInputReceipt] = [:]
    private var loadError: String?

    init(store: URL) {
        file = store.appendingPathComponent("terminal-input-receipts.json")
        if FileManager.default.fileExists(atPath: file.path) {
            do { receipts = try JSONDecoder().decode([String: TerminalInputReceipt].self, from: Data(contentsOf: file)) }
            catch { loadError = "無法讀取先前的 Terminal 送出紀錄，未送出，避免重複執行。" }
        }
    }
    static func fingerprint(session: String, binding: String, text: String) -> String {
        let data = try! JSONEncoder().encode([session, binding, text])
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
    func previous(_ requestID: String, fingerprint: String) throws -> [String: Any]? {
        if let loadError { throw PilotError(message: loadError) }
        guard let receipt = receipts[requestID] else { return nil }
        guard receipt.fingerprint == fingerprint else { throw PilotError(message: "此送出識別碼已用於另一筆 Terminal 輸入，未重送。") }
        return ["sent": receipt.sent, "uncertain": !receipt.sent]
    }
    func record(_ requestID: String, fingerprint: String, sent: Bool) throws {
        if let loadError { throw PilotError(message: loadError) }
        receipts[requestID] = TerminalInputReceipt(fingerprint: fingerprint, sent: sent)
        do {
            try JSONEncoder().encode(receipts).write(to: file, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
        } catch {
            // A persistence failure must fail closed for this instance too.
            loadError = "Terminal 送出紀錄無法儲存，請先查看目前內容，避免重複執行。"
            throw error
        }
    }
}

extension TerminalBridge {
    static func sendTerminal(_ tab: TerminalTab, text: String) async throws {
        // `do script ... in t` writes into the exact existing terminal; it does
        // not run a new shell or change focus. Terminal appends Return.
        try await target(tab, commands: "do script \(quote(text)) in t")
    }
}
