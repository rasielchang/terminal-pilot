import SwiftUI

struct WebSettingsView: View {
    @ObservedObject var model: PilotModel
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Label("本地工作網頁", systemImage: "globe").font(.title2.bold())
            Text("用聊天介面查看與回覆原本的 Codex／Claude Code 工作。Terminal Pilot 需保持開啟。")
                .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Toggle("允許區網／Tailscale 裝置連線", isOn: $model.webLAN)
                .onChange(of: model.webLAN) { _ in model.startWeb() }
            Toggle("iPhone 通知（Tailscale HTTPS）", isOn: $model.webHTTPS)
                .onChange(of: model.webHTTPS) { _ in model.startWeb() }
            if model.webHTTPS {
                Text("iPhone 用下方 HTTPS 網址加入主畫面，再開啟通知。僅限你的 Tailscale 網路；Mac 需保持運作。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            Text(model.webLAN ? "手機開啟下方電腦位址，輸入配對碼。區網使用 HTTP；跨網路請走 Tailscale。" : model.webHTTPS ? "手機使用 Tailscale HTTPS；一般 HTTP 僅接受這台 Mac 連線。" : "目前只接受這台 Mac 的瀏覽器連線。")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if model.webStarting {
                ProgressView("正在啟動…")
            } else if !model.webURLs.isEmpty {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(model.webURLs, id: \.self) { address in
                        Text(address).font(.system(.body, design: .monospaced)).textSelection(.enabled)
                    }
                    Divider()
                    HStack {
                        Text("配對碼").foregroundStyle(.secondary)
                        Text(model.webPairingCode).font(.system(size: 26, weight: .semibold, design: .monospaced)).textSelection(.enabled)
                        Spacer()
                        Button("更新配對碼") { model.refreshWebPairing() }.font(.caption)
                    }
                    Text("配對碼 10 分鐘有效，更新碼不影響已連線裝置。重設配對會撤銷所有連線及手機通知。")
                        .font(.caption).foregroundStyle(.secondary)
                }.padding(14).frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.white.opacity(0.05), in: RoundedRectangle(cornerRadius: 10))
                Button("在這台 Mac 開啟網頁") { model.openWeb() }.buttonStyle(.borderedProminent).tint(.mint)
            }
            if let error = model.webError { Text(error).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true) }
            HStack {
                Button(model.webURLs.isEmpty ? "啟動網頁" : "重設配對") { model.startWeb(resetAuth: !model.webURLs.isEmpty) }.disabled(model.webStarting)
                if !model.webURLs.isEmpty { Button("停止服務") { model.stopWeb() } }
                Spacer()
                Button("完成") { model.showWeb = false }.keyboardShortcut(.cancelAction)
            }
        }.padding(24).frame(width: 430).preferredColorScheme(.dark)
    }
}
