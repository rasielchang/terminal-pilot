import AppKit

/// The signed native app owns Terminal access; the child only serves HTTP and reads agent history.
@MainActor final class LocalWebBridge {
    private var process: Process?
    private var input: Pipe?
    private var output: Pipe?
    private var buffer = Data()
    private var generation = UUID()
    private let writer = DispatchQueue(label: "local.terminal-pilot.web-replies")

    func start(lan: Bool, https: Bool = false, resetAuth: Bool = false, pairing: @escaping (String) -> Void,
               ready: @escaping ([String: Any]) -> Void,
               failed: @escaping (String) -> Void,
               request: @escaping (String, [String: Any]) async throws -> [String: Any]) throws {
        stop()
        guard let resource = Bundle.main.resourceURL else { throw PilotError(message: "找不到網頁服務。") }
        let child = Process(), stdin = Pipe(), stdout = Pipe()
        child.executableURL = URL(fileURLWithPath: try RuntimeSupport.python(resource: resource))
        child.environment = RuntimeSupport.environment(resource: resource)
        // Runtime bytecode must never modify the signed app's sealed resources.
        child.arguments = ["-B", resource.appendingPathComponent("web_server.py").path] + (lan ? ["--lan"] : [])
            + (https ? ["--tailscale"] : []) + (resetAuth ? ["--reset-auth"] : [])
        child.standardInput = stdin; child.standardOutput = stdout
        child.standardError = FileHandle.nullDevice
        input = stdin; output = stdout; process = child
        let current = generation
        stdout.fileHandleForReading.readabilityHandler = { [weak self] handle in
            guard let self else { return }
            let data = handle.availableData
            Task { @MainActor in
                guard self.generation == current, !data.isEmpty else { return }
                self.buffer.append(data)
                while let end = self.buffer.firstIndex(of: 10) {
                    let line = self.buffer.prefix(upTo: end)
                    self.buffer.removeSubrange(...end)
                    guard let message = (try? JSONSerialization.jsonObject(with: line)) as? [String: Any] else { continue }
                    if message["event"] as? String == "ready" { ready(message); continue }
                    if message["event"] as? String == "pairing", let code = message["pairingCode"] as? String { pairing(code); continue }
                    guard let id = message["id"] as? String, let method = message["method"] as? String else { continue }
                    Task { @MainActor in
                        var response: [String: Any] = ["id": id]
                        var params = message["params"] as? [String: Any] ?? [:]
                        params["_deadline"] = message["deadline"]
                        do { response["result"] = try await request(method, params) }
                        catch { response["error"] = error.localizedDescription }
                        guard self.generation == current, let bytes = try? JSONSerialization.data(withJSONObject: response) else { return }
                        self.writer.async { try? stdin.fileHandleForWriting.write(contentsOf: bytes + Data([10])) }
                    }
                }
            }
        }
        child.terminationHandler = { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in
                guard self.generation == current else { return }
                self.stop(); failed("本地網頁服務已停止，請重新啟動。")
            }
        }
        do { try child.run() }
        catch { stop(); throw error }
    }

    func refreshPairing() {
        guard let input else { return }
        let bytes = Data("{\"command\":\"refresh-pairing\"}\n".utf8)
        writer.async { try? input.fileHandleForWriting.write(contentsOf: bytes) }
    }

    func stop() {
        generation = UUID()
        output?.fileHandleForReading.readabilityHandler = nil
        try? input?.fileHandleForWriting.close()
        if process?.isRunning == true { process?.terminate() }
        process = nil; input = nil; output = nil; buffer.removeAll()
    }
}
