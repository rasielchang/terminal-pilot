import Foundation

/// Resolve tools on the computer running Pilot, never on the computer that built it.
enum RuntimeSupport {
    static func isExecutable(_ path: String) -> Bool {
        var directory: ObjCBool = false
        return path.hasPrefix("/") && FileManager.default.fileExists(atPath: path, isDirectory: &directory)
            && !directory.boolValue && FileManager.default.isExecutableFile(atPath: path)
    }
    static let toolNames = ["codex", "claude", "tailscale"]
    static var store: URL {
        ProcessInfo.processInfo.environment["TERMINAL_PILOT_STORE"].map { URL(fileURLWithPath: $0) }
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Terminal Pilot")
    }
    static var overrides: [String: String] {
        guard let data = try? Data(contentsOf: store.appendingPathComponent("tools.json")) else { return [:] }
        return (try? JSONDecoder().decode([String: String].self, from: data)) ?? [:]
    }
    static func directories(home: URL = FileManager.default.homeDirectoryForCurrentUser,
                            path: String = ProcessInfo.processInfo.environment["PATH"] ?? "") -> [String] {
        var values = path.split(separator: ":").map(String.init).filter { $0.hasPrefix("/") }
        values += [".local/bin", ".volta/bin", ".npm-global/bin", ".cargo/bin"].map { home.appendingPathComponent($0).path }
        values += ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
        let nvm = home.appendingPathComponent(".nvm/versions/node")
        let versions = (try? FileManager.default.contentsOfDirectory(atPath: nvm.path)) ?? []
        values += versions.sorted { $0.compare($1, options: .numeric) == .orderedDescending }
            .map { nvm.appendingPathComponent($0).appendingPathComponent("bin").path }
        var seen = Set<String>()
        return values.filter { seen.insert($0).inserted }
    }
    static func executable(_ name: String, resource: URL? = Bundle.main.resourceURL) -> String? {
        let fm = FileManager.default
        if let configured = overrides[name] { return isExecutable(configured) ? configured : nil }
        if name == "node", let bundled = resource?.appendingPathComponent("runtime/node/bin/node"),
           fm.isExecutableFile(atPath: bundled.path) { return bundled.path }
        for directory in directories() {
            let candidate = URL(fileURLWithPath: directory).appendingPathComponent(name).path
            if fm.isExecutableFile(atPath: candidate) { return candidate }
        }
        if name == "tailscale" {
            let app = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
            if fm.isExecutableFile(atPath: app) { return app }
        }
        return nil
    }
    static func python(resource: URL) throws -> String {
        let bundled = resource.appendingPathComponent("runtime/python/bin/python3").path
        if FileManager.default.isExecutableFile(atPath: bundled) { return bundled }
        // Source builds explicitly opt into the developer's interpreter. Public bundles do not.
        if let data = try? Data(contentsOf: resource.appendingPathComponent("runtime.json")),
           let values = try? JSONDecoder().decode([String: String].self, from: data),
           let python = values["python"], FileManager.default.isExecutableFile(atPath: python) { return python }
        throw PilotError(message: "找不到隨附的執行環境，請重新下載完整的 Terminal Pilot App。")
    }
    static func requireTool(_ name: String) throws {
        guard executable(name) != nil else {
            throw PilotError(message: "尚未找到 \(name)。請先安裝並登入，或到 Mac 的「環境設定」選擇執行檔，再開啟工作。")
        }
    }
    static func environment(resource: URL? = Bundle.main.resourceURL) -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        env.removeValue(forKey: "PYTHONPATH"); env.removeValue(forKey: "PYTHONHOME")
        env["PYTHONNOUSERSITE"] = "1"; env["PYTHONDONTWRITEBYTECODE"] = "1"
        var paths = directories()
        if let node = executable("node", resource: resource) { paths.insert(URL(fileURLWithPath: node).deletingLastPathComponent().path, at: 0) }
        env["PATH"] = paths.joined(separator: ":")
        return env
    }
    static func saveTool(_ name: String, path: String?) throws {
        guard toolNames.contains(name) else { throw PilotError(message: "不支援的工具。") }
        if let path, !isExecutable(path) {
            throw PilotError(message: "請選擇可執行的 CLI 檔案。")
        }
        var values = overrides; values[name] = path
        try FileManager.default.createDirectory(at: store, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let file = store.appendingPathComponent("tools.json")
        try JSONEncoder().encode(values).write(to: file, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
    }
}
