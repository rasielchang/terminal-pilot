import Foundation
import Security
import LocalAuthentication

enum VoiceKeychain {
    // Retains existing users' service/account; new installations use the current account.
    static let service = "openai-platform-api"
    static var query: [String: Any] { [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service] }
    static func account() -> String? {
        var query = query
        query[kSecReturnAttributes as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        let context = LAContext(); context.interactionNotAllowed = true
        query[kSecUseAuthenticationContext as String] = context
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess else { return nil }
        return (result as? [String: Any])?[kSecAttrAccount as String] as? String
    }
    static func save(_ key: String) throws {
        let account = account() ?? NSUserName()
        var query = query; query[kSecAttrAccount as String] = account
        let values: [String: Any] = [kSecValueData as String: Data(key.utf8)]
        var status = SecItemUpdate(query as CFDictionary, values as CFDictionary)
        if status == errSecItemNotFound {
            query.merge(values) { _, new in new }
            status = SecItemAdd(query as CFDictionary, nil)
        }
        guard status == errSecSuccess else { throw PilotError(message: "無法儲存至 macOS 鑰匙圈（\(status)）。") }
        query.removeValue(forKey: kSecValueData as String)
        query[kSecReturnData as String] = true
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              result as? Data == Data(key.utf8) else {
            throw PilotError(message: "金鑰寫入後無法讀回確認，請檢查鑰匙圈存取權限。")
        }
    }
    static func validate(_ key: String) async throws {
        guard !key.isEmpty, key.count <= 1024, !key.contains(where: { $0.isWhitespace }) else {
            throw PilotError(message: "請輸入完整的 API key。")
        }
        var request = URLRequest(url: URL(string: "https://api.openai.com/v1/models")!)
        request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        request.timeoutInterval = 20
        let session = URLSession(configuration: .ephemeral, delegate: NoKeyRedirect(), delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        let (_, response) = try await session.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else {
            throw PilotError(message: "OpenAI 未接受此金鑰，請確認 API key 與帳號權限；既有金鑰未變更。")
        }
    }
}

private final class NoKeyRedirect: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}
