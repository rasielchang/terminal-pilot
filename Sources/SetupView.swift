import SwiftUI
import AppKit

struct SetupView: View {
    @ObservedObject var model: PilotModel
    @State private var tools: [String: String] = [:]
    @State private var key = ""
    @State private var hasKey = false
    @State private var busy = false
    @State private var note = ""
    private let links = ["codex": "https://developers.openai.com/codex/cli/",
                         "claude": "https://code.claude.com/docs/en/setup",
                         "tailscale": "https://tailscale.com/download/mac"]
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("開始使用 Terminal Pilot").font(.title2.bold())
            Text("早期預覽版 · macOS 13+ / Apple Silicon")
                .font(.caption).foregroundStyle(.secondary)
            Text("既有 Terminal 視窗會自動出現在工作列表。第一次操作時，請允許 Pilot 控制 Terminal；一般視窗管理不需要安裝 AI 工具。")
                .fixedSize(horizontal: false, vertical: true)
            Divider()
            Text("選用工具").font(.headline)
            ForEach(RuntimeSupport.toolNames, id: \.self) { name in
                VStack(alignment: .leading, spacing: 5) {
                    HStack {
                        Label(name == "claude" ? "Claude Code" : name == "codex" ? "Codex CLI" : "Tailscale",
                              systemImage: tools[name] == nil ? "circle" : "checkmark.circle.fill")
                            .foregroundStyle(tools[name] == nil ? Color.secondary : .mint)
                        Spacer()
                        if tools[name] == nil { Link("安裝說明", destination: URL(string: links[name]!)!) }
                        Button("選擇執行檔…") { choose(name) }
                        if RuntimeSupport.overrides[name] != nil { Button("自動尋找") { save(name, path: nil) } }
                    }
                    Text(tools[name] ?? "尚未找到；安裝並登入後，按重新檢查。")
                        .font(.caption).foregroundStyle(.secondary).lineLimit(2).textSelection(.enabled)
                }
            }
            Text("手機與本地網頁：到主視窗右下角「網頁」啟動、配對。外出連線與 iPhone 推播需要 Tailscale。")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Divider()
            Text("語音（選用）").font(.headline)
            Text("錄音轉文字、摘要與朗讀使用你自己的 OpenAI API 額度。使用時會上傳錄音或所選回覆；金鑰僅存於這台 Mac 的鑰匙圈。")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if hasKey { Label("已設定金鑰", systemImage: "checkmark.circle").foregroundStyle(.mint) }
            HStack {
                SecureField(hasKey ? "輸入新 API key 以更新" : "OpenAI API key", text: $key)
                Button(busy ? "驗證中…" : "驗證並儲存") {
                    busy = true; note = ""
                    Task { @MainActor in
                        do {
                            try await VoiceKeychain.validate(key)
                            try VoiceKeychain.save(key)
                            key = ""; hasKey = true; note = "金鑰已存入鑰匙圈。"
                        } catch { note = error.localizedDescription }
                        busy = false
                    }
                }.disabled(busy || key.isEmpty)
            }
            if !note.isEmpty { Text(note).font(.caption).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true) }
            HStack {
                Button("重新檢查") { refresh(); if !model.webURLs.isEmpty { model.startWeb() } }
                Spacer()
                Button("完成") { key = ""; model.showSetup = false }.keyboardShortcut(.cancelAction).disabled(busy)
            }
        }.padding(24).frame(width: 510).preferredColorScheme(.dark)
            .onAppear { refresh() }
    }
    private func refresh() {
        tools = Dictionary(uniqueKeysWithValues: RuntimeSupport.toolNames.compactMap { name in RuntimeSupport.executable(name).map { (name, $0) } })
        hasKey = VoiceKeychain.account() != nil
    }
    private func choose(_ name: String) {
        let panel = NSOpenPanel(); panel.canChooseDirectories = false; panel.allowsMultipleSelection = false
        panel.showsHiddenFiles = true; panel.message = "選擇你已安裝並信任的 \(name) CLI 執行檔。"
        if panel.runModal() == .OK, let url = panel.url { save(name, path: url.path) }
    }
    private func save(_ name: String, path: String?) {
        do {
            try RuntimeSupport.saveTool(name, path: path); refresh()
            if !model.webURLs.isEmpty { model.startWeb() }
        } catch { note = error.localizedDescription }
    }
}
