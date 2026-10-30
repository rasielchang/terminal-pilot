import SwiftUI
import AppKit

struct PilotView: View {
    @ObservedObject var model: PilotModel
    var body: some View {
        VStack(spacing: 0) {
            header
            if let error = model.error {
                VStack(alignment: .leading, spacing: 8) {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .font(.system(size: 12)).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
                    HStack {
                        Button("重新連接") { Task { await model.refresh(force: true) } }
                        Button("自動化設定") {
                            if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation") { NSWorkspace.shared.open(url) }
                        }
                    }.font(.system(size: 11))
                }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.orange.opacity(0.08))
            }
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Label("Brain · 個人助理", systemImage: "sparkles").font(.system(size: 14, weight: .semibold))
                    if model.brainUnreadCount > 0 {
                        Circle().fill(Color.red).frame(width: 9, height: 9)
                            .accessibilityLabel("Brain 有 \(model.brainUnreadCount) 個未讀提醒")
                    }
                    Spacer()
                    Button(model.brainOpening ? "接續中…" : model.brainRow?.tab != nil ? "定位 Brain" : model.brainThread.isEmpty ? "開啟 Brain" : "續接原對話") { model.locateBrain() }
                        .disabled(model.brainOpening || model.acting)
                }
                HStack {
                    Text(model.brainStatus)
                        .font(.system(size: 12))
                        .foregroundStyle(model.brainRow.map { model.brainNeedsHuman($0) } == true ? Color.orange : model.brainUnreadCount > 0 ? Color.red : Color.secondary)
                    Spacer(minLength: 8)
                    if let row = model.brainRow, row.unread {
                        Button("已處理") { model.markRead(row) }
                            .font(.system(size: 11)).buttonStyle(.bordered).tint(.mint)
                            .help("清除 Brain 當下提醒；不會核准或回答等待中的要求")
                    }
                }
                if model.brainPending > 0 { Text("\(model.brainPending) 個工作等待本人決定").font(.system(size: 11)).foregroundStyle(.orange) }
                if model.brainRow != nil && !model.brainDetail.isEmpty { Text(model.brainDetail).font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(2) }
            }.padding(14)
                .background(model.brainUnreadCount > 0 ? Color(red: 0.16, green: 0.105, blue: 0.12) : Color.mint.opacity(0.07), in: RoundedRectangle(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(model.brainUnreadCount > 0 ? Color.red.opacity(0.4) : Color.clear, lineWidth: 1))
                .padding(.horizontal, 16).padding(.top, 12)
            Picker("列表", selection: $model.showingProjects) {
                Text("工作視窗").tag(false)
                Text("專案紀錄").tag(true)
            }.pickerStyle(.segmented).labelsHidden().padding(.horizontal, 16).padding(.top, 14)
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                TextField("搜尋工作或專案", text: $model.query).textFieldStyle(.plain)
                if !model.query.isEmpty { Button { model.query = "" } label: { Image(systemName: "xmark.circle.fill") }.buttonStyle(.plain) }
            }.padding(10).background(Color.white.opacity(0.045), in: RoundedRectangle(cornerRadius: 8)).padding(.horizontal, 16).padding(.top, 14)
            if model.showingProjects {
                HStack {
                    Text("固定優先 · 最近開啟在前")
                    Spacer()
                    Text("\(model.preferences.projects.count) 個專案")
                }.font(.system(size: 11)).foregroundStyle(.secondary).padding(.horizontal, 16).padding(.vertical, 12)
                ProjectListView(model: model)
            } else {
                HStack {
                filter("全部", active: !model.unreadOnly) { model.unreadOnly = false }
                filter("待處理 \(model.unreadCount)", active: model.unreadOnly) { model.unreadOnly = true }
                Spacer()
                Text("\(model.windowCount) 個視窗").font(.system(size: 11)).foregroundStyle(.secondary)
            }.padding(.horizontal, 16).padding(.vertical, 12)
            Text("需要你處理的在前 · 最新通知／活動在前")
                .font(.system(size: 10)).foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 18).padding(.bottom, 10)
            ScrollView {
                LazyVStack(spacing: 9) {
                    if model.filteredRows.isEmpty {
                        VStack(spacing: 12) {
                            Image(systemName: model.unreadOnly ? "checkmark.circle" : "macwindow").font(.system(size: 30)).foregroundStyle(.mint)
                            Text(!model.query.isEmpty ? "找不到符合的工作" : model.unreadOnly ? (model.brainUnreadCount > 0 ? "Brain 的提醒在上方" : "目前沒有需要你處理的工作") : "把工作放到這裡").font(.system(size: 15, weight: .semibold))
                            Text(!model.query.isEmpty ? "試試其他名稱或資料夾路徑。" : model.unreadOnly ? (model.brainUnreadCount > 0 ? "可定位 Brain 查看，或將當下提醒標為已處理。" : "完成待查看或需要確認時會亮起提醒。") : "既有 Terminal 視窗會自動出現。\n也可以新增一般 Terminal 或 AI 工作。")
                                .font(.system(size: 12)).foregroundStyle(.secondary).multilineTextAlignment(.center)
                        }.frame(maxWidth: .infinity).padding(.vertical, 60)
                    }
                    ForEach(model.filteredRows) { row in PilotRow(model: model, row: row) }
                }.padding(.horizontal, 16).padding(.bottom, 16)
            }
            }
            Divider().opacity(0.4)
            HStack(spacing: 8) {
                if !model.showingProjects {
                Button { model.arrange() } label: { Label("並排 \(model.selectedCount > 0 ? "(\(model.selectedCount))" : "")", systemImage: "rectangle.split.2x1") }
                    .disabled(model.selectedCount == 0 || model.acting)
                Button("還原排列") { model.restoreLayout() }.disabled(!model.layoutAvailable || model.acting)
                } else {
                    Text("新工作自動記錄，關閉視窗後仍保留")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button { model.showSetup = true } label: { Image(systemName: "gearshape") }.help("環境設定與語音金鑰")
                Button { model.showWebSettings() } label: { Label("網頁", systemImage: "globe") }.help("本地工作網頁")
                Button { model.showHelp = true } label: { Image(systemName: "questionmark.circle") }.buttonStyle(.plain).help("使用方式")
            }.font(.system(size: 11)).padding(14)
            Text("macOS Terminal · 獨立視窗，集中管理")
                .font(.system(size: 10)).foregroundStyle(.secondary).padding(.bottom, 12)
        }
        .frame(minWidth: 370, minHeight: 490)
        .background(Color(red: 0.075, green: 0.086, blue: 0.103))
        .preferredColorScheme(.dark)
        .sheet(isPresented: $model.showNew) { NewTaskView(model: model) }
        .sheet(isPresented: $model.showHelp) { HelpView(model: model) }
        .sheet(isPresented: $model.showSetup) { SetupView(model: model) }
        .sheet(isPresented: $model.showWeb) { WebSettingsView(model: model) }
        .sheet(item: $model.projectToSave) { row in SaveProjectView(model: model, row: row) }
        .sheet(item: $model.projectToConfigure) { project in AgentProjectSettingsView(model: model, project: project) }
        .sheet(item: $model.projectToOpen) { project in OpenProjectView(model: model, project: project) }
    }
    var header: some View {
        VStack(alignment: .leading, spacing: 15) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("TERMINAL PILOT").font(.system(size: 10, weight: .bold, design: .monospaced)).tracking(2).foregroundStyle(.mint)
                    HStack(spacing: 9) {
                        Text(model.showingProjects ? "專案紀錄" : "工作視窗").font(.system(size: 24, weight: .semibold))
                        if model.unreadCount > 0 {
                            Text(model.unreadCount > 99 ? "99+" : String(model.unreadCount))
                                .font(.system(size: 12, weight: .bold, design: .rounded))
                                .foregroundStyle(.white).padding(.horizontal, 7).frame(minWidth: 23, minHeight: 23)
                                .background(Color.red, in: Capsule())
                                .accessibilityLabel("\(model.unreadCount) 個待處理工作")
                        }
                    }
                }
                Spacer()
                Button { model.togglePin() } label: { Image(systemName: model.pinned ? "pin.fill" : "pin") }.buttonStyle(.plain)
                    .foregroundStyle(model.pinned ? Color.mint : Color.secondary).help("\(model.pinned ? "取消" : "啟用")置頂")
                Button { Task { await model.refresh(force: true) } } label: { Image(systemName: "arrow.clockwise") }.buttonStyle(.plain).help("重新整理")
            }
            HStack {
                HStack(spacing: 7) {
                    Text(model.unreadCount > 0 ? "\(model.unreadCount) 個工作需要你" : "需要你處理時會亮起提醒")
                        .font(.system(size: 12)).foregroundStyle(.secondary)
                }
                Spacer()
                Button { model.showNew = true } label: { Label("新工作", systemImage: "plus").font(.system(size: 12, weight: .semibold)) }
                    .buttonStyle(.borderedProminent).tint(.mint).foregroundStyle(.black).disabled(model.acting)
            }
        }.padding(.horizontal, 18).padding(.top, 16).padding(.bottom, 16)
            .background(Color.white.opacity(0.02))
    }
    func filter(_ title: String, active: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) { Text(title).font(.system(size: 11, weight: .medium)).padding(.horizontal, 11).padding(.vertical, 6)
            .background(active ? Color.white.opacity(0.11) : Color.clear, in: Capsule()) }
            .buttonStyle(.plain).foregroundStyle(active ? Color.primary : Color.secondary)
    }
}

struct PilotRow: View {
    @ObservedObject var model: PilotModel
    let row: Row
    @State private var renaming = false
    @State private var name = ""
    var body: some View {
        card
            .contentShape(RoundedRectangle(cornerRadius: 10))
            .onTapGesture(count: 2) { model.focus(row) }
            .help("雙擊叫回視窗並清除當下提醒\n" + (model.directory(for: row).isEmpty ? row.tab?.tty ?? "" : model.directory(for: row)))
    }
    private var card: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 10) {
                if row.tab != nil {
                    Button {
                        if model.selection.contains(row.id) { model.selection.remove(row.id) } else { model.selection.insert(row.id) }
                    } label: { Image(systemName: model.selection.contains(row.id) ? "checkmark.square.fill" : "square")
                        .foregroundStyle(model.selection.contains(row.id) ? Color.mint : Color.secondary) }
                        .buttonStyle(.plain).help("選取此視窗以排列").padding(.top, 2)
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text(row.title).font(.system(size: 13, weight: .semibold)).lineLimit(2).frame(maxWidth: .infinity, alignment: .leading)
                    Text(row.event?.toolName ?? "Terminal").font(.system(size: 10, weight: .medium, design: .monospaced)).foregroundStyle(.secondary)
                }
                if row.unread { Circle().fill(row.tint).frame(width: 9, height: 9).padding(.top, 4).accessibilityLabel("需要你處理") }
                Menu {
                    Button("重新命名") { name = row.title; renaming = true }
                    Button(model.project(for: row) == nil ? "儲存為專案…" : "專案設定…") { model.projectToSave = row }
                    Button("移到左側") { model.side(row, right: false) }.disabled(row.tab == nil)
                    Button("移到右側") { model.side(row, right: true) }.disabled(row.tab == nil)
                    if !model.directory(for: row).isEmpty { Button("在 Finder 開啟專案") { NSWorkspace.shared.open(URL(fileURLWithPath: model.directory(for: row))) } }
                } label: { Image(systemName: "ellipsis") }.menuStyle(.borderlessButton).frame(width: 18)
            }
            HStack(spacing: 6) {
                Image(systemName: row.phase == "running" ? "arrow.triangle.2.circlepath" : row.phase == "complete" ? "checkmark.circle" : row.phase == "waiting" ? "hand.raised" : "circle.dotted")
                Text(row.status)
                Spacer(minLength: 0)
                Text(row.tab == nil ? "視窗已關閉" : row.tab!.minimized ? "已收起" : "桌面上")
                    .foregroundStyle(.secondary)
            }.font(.system(size: 11)).foregroundStyle(row.tint)
            if let event = row.event, event.noticeAt > 0 {
                Text("最近通知：\(Date(timeIntervalSince1970: event.noticeAt), style: .relative)前")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
            }
            if let event = row.event, event.tool == "codex", !event.lifecycleReady, event.codexSocket?.isEmpty != false {
                Text(event.lifecycle == "pending"
                     ? "自動清除待啟用：在 Codex 輸入 /hooks，信任 Terminal Pilot 的 codex-hook；下次送出後接通。"
                     : "此工作僅有完成通知；請從專案紀錄重新續接，以啟用送出後自動清除。")
                    .font(.system(size: 10)).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: 8) {
                Button("叫回") { model.focus(row) }.disabled(row.tab == nil || model.acting)
                    .help("叫回正確視窗與分頁，成功後清除當下提醒")
                Button("收起") { model.minimize(row) }.disabled(row.tab == nil || model.tabMinimized(row) || model.acting)
                Spacer()
                if row.unread { Button("已處理") { model.markRead(row) }.tint(.mint) }
            }.font(.system(size: 11)).buttonStyle(.bordered)
        }.padding(12)
            .background(row.unread ? Color(red: 0.16, green: 0.105, blue: 0.12) : Color.white.opacity(0.04), in: RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(row.unread ? Color.red.opacity(0.4) : Color.white.opacity(0.065), lineWidth: 1))
            .alert("重新命名", isPresented: $renaming) {
                TextField("工作名稱", text: $name)
                Button("取消", role: .cancel) {}
                Button("儲存") { model.rename(row, name: name) }
            }
    }
}

extension PilotModel {
    func tabMinimized(_ row: Row) -> Bool { row.tab?.minimized ?? false }
}

struct NewTaskView: View {
    @ObservedObject var model: PilotModel
    @State private var tool = "terminal"
    @State private var directory = ""
    @State private var label = ""
    @State private var pinned = false
    @State private var session = AgentSession()
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("新增工作").font(.system(size: 21, weight: .semibold))
            Text("開啟 macOS Terminal 的獨立視窗。可以單純使用 shell，也可以啟動 AI。")
                .font(.system(size: 12)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Picker("工作類型", selection: $tool) {
                Text("一般 Terminal").tag("terminal")
                Text("Claude Code").tag("claude")
                Text("Codex CLI").tag("codex")
            }.pickerStyle(.segmented).labelsHidden()
            if tool != "terminal" { AgentSessionFields(session: $session, tool: tool) }
            VStack(alignment: .leading, spacing: 7) {
                Text("專案資料夾").font(.system(size: 12, weight: .medium))
                HStack {
                    Text(directory).font(.system(size: 11, design: .monospaced)).lineLimit(2).frame(maxWidth: .infinity, alignment: .leading)
                    Button("選擇…") {
                        let panel = NSOpenPanel(); panel.canChooseFiles = false; panel.canChooseDirectories = true; panel.allowsMultipleSelection = false
                        panel.directoryURL = URL(fileURLWithPath: directory)
                        if panel.runModal() == .OK, let url = panel.url { directory = url.path; if label.isEmpty { label = url.lastPathComponent } }
                    }
                }
            }
            TextField("工作名稱（例如：登入問題）", text: $label).textFieldStyle(.roundedBorder)
            Toggle("固定此專案，方便下次開啟", isOn: $pinned).font(.system(size: 12))
            Text("開啟後會自動保存到「專案紀錄」。")
                .font(.system(size: 11)).foregroundStyle(.secondary)
            Text(tool == "terminal" ? "只開 Terminal 並切到選定資料夾，不啟動 AI。" : tool == "codex" ? "沿用 Codex 的 Terminal 介面，並接通手機核准；送出回覆或恢復工作會清除提醒。" : "Claude 送出回覆或恢復工作會清除提醒；等待確認與完成時再次提醒。")
                .font(.system(size: 11)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack {
                Button("取消") { model.showNew = false }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("開啟 Terminal") {
                    model.showNew = false
                    model.newTask(tool: tool, directory: directory, label: label.isEmpty ? URL(fileURLWithPath: directory).lastPathComponent : label,
                                  pinned: pinned, session: tool != "terminal" ? session : nil)
                }.keyboardShortcut(.defaultAction).buttonStyle(.borderedProminent).tint(.mint).foregroundStyle(.black)
                    .disabled(model.acting || directory.isEmpty || (tool != "terminal" && !session.isValid))
            }
        }.padding(24).frame(width: 365).onAppear { directory = model.preferences.lastDirectory }
    }
}

struct ProjectListView: View {
    @ObservedObject var model: PilotModel
    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 9) {
                if model.filteredProjects.isEmpty {
                    VStack(spacing: 12) {
                        Image(systemName: "folder.badge.clock").font(.system(size: 30)).foregroundStyle(.mint)
                        Text(model.query.isEmpty ? "留下專案，下次一鍵開啟" : "找不到符合的專案")
                            .font(.system(size: 15, weight: .semibold))
                        Text(model.query.isEmpty ? "從「新工作」開啟後會自動記錄。\n既有視窗可從 … 選擇「儲存為專案」。" : "試試其他名稱或資料夾路徑。")
                            .font(.system(size: 12)).foregroundStyle(.secondary).multilineTextAlignment(.center)
                    }.frame(maxWidth: .infinity).padding(.vertical, 50)
                }
                let pinned = model.filteredProjects.filter(\.pinned)
                let recent = model.filteredProjects.filter { !$0.pinned }
                if !pinned.isEmpty {
                    Label("固定專案", systemImage: "pin.fill").font(.system(size: 11, weight: .medium)).foregroundStyle(.mint).padding(.bottom, 3)
                    ForEach(pinned) { ProjectRow(model: model, project: $0) }
                }
                if !recent.isEmpty {
                    Label("最近開啟", systemImage: "clock").font(.system(size: 11, weight: .medium)).foregroundStyle(.secondary)
                        .padding(.top, pinned.isEmpty ? 0 : 12).padding(.bottom, 3)
                    ForEach(recent) { ProjectRow(model: model, project: $0) }
                }
            }.padding(.horizontal, 16).padding(.bottom, 16)
        }
    }
}

struct ProjectRow: View {
    @ObservedObject var model: PilotModel
    let project: SavedProject
    @State private var renaming = false
    @State private var removing = false
    @State private var name = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "folder").foregroundStyle(.mint).padding(.top, 2)
                VStack(alignment: .leading, spacing: 4) {
                    Text(project.name).font(.system(size: 13, weight: .semibold)).lineLimit(2)
                    Text(project.toolName).font(.system(size: 10, design: .monospaced)).foregroundStyle(.secondary)
                    if project.isAgent {
                        Text("開啟時選擇新對話或續接").font(.system(size: 10)).foregroundStyle(.mint)
                    }
                }.frame(maxWidth: .infinity, alignment: .leading)
                Button { model.toggleProjectPin(project) } label: { Image(systemName: project.pinned ? "pin.fill" : "pin") }
                    .buttonStyle(.plain).foregroundStyle(project.pinned ? Color.mint : Color.secondary)
                    .help(project.pinned ? "取消固定專案" : "固定專案")
                    .accessibilityLabel(project.pinned ? "取消固定專案" : "固定專案")
                Menu {
                    Button("重新命名") { name = project.name; renaming = true }
                    if project.isAgent {
                        Button("對話啟動方式…") { model.projectToConfigure = project }
                    }
                    Button("更換資料夾…") {
                        let panel = NSOpenPanel(); panel.canChooseFiles = false; panel.canChooseDirectories = true; panel.allowsMultipleSelection = false
                        panel.directoryURL = URL(fileURLWithPath: project.directory)
                        if panel.runModal() == .OK, let url = panel.url { model.changeProjectDirectory(project, directory: url.path) }
                    }
                    Button("在 Finder 開啟專案") { NSWorkspace.shared.open(URL(fileURLWithPath: project.directory)) }
                    Divider()
                    Button("移除紀錄…", role: .destructive) { removing = true }
                } label: { Image(systemName: "ellipsis") }.menuStyle(.borderlessButton).frame(width: 18)
            }
            Text(project.directory).font(.system(size: 10, design: .monospaced)).foregroundStyle(.secondary)
                .lineLimit(2).truncationMode(.middle).help(project.directory)
            HStack {
                Text("最近開啟：\(Date(timeIntervalSince1970: project.lastOpenedAt), style: .relative)前")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
                Spacer()
                Button { model.reopenProject(project) } label: { Label("開啟", systemImage: "arrow.up.forward") }
                    .font(.system(size: 11)).buttonStyle(.bordered).tint(.mint).disabled(model.acting)
                    .help(project.isAgent ? "選擇新對話或續接先前的對話" : "在此資料夾開啟新的 \(project.toolName) 工作")
            }
        }.padding(12)
            .background(Color.white.opacity(0.04), in: RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(project.pinned ? Color.mint.opacity(0.25) : Color.white.opacity(0.065), lineWidth: 1))
            .alert("重新命名專案", isPresented: $renaming) {
                TextField("專案名稱", text: $name)
                Button("取消", role: .cancel) {}
                Button("儲存") { model.renameProject(project, name: name) }
            }
            .alert("移除「\(project.name)」的紀錄？", isPresented: $removing) {
                Button("取消", role: .cancel) {}
                Button("移除紀錄", role: .destructive) { model.removeProject(project) }
            } message: { Text("只移除這筆專案紀錄，資料夾與開著的 Terminal 都會保留。") }
    }
}

struct SaveProjectView: View {
    @ObservedObject var model: PilotModel
    let row: Row
    @State private var name = ""
    @State private var directory = ""
    @State private var pinned = true
    @State private var session = AgentSession(mode: .picker)
    private var tool: String { model.project(for: row)?.tool ?? row.event?.tool ?? "terminal" }
    private var isAgent: Bool { ["codex", "claude"].contains(tool) }
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("儲存專案").font(.system(size: 21, weight: .semibold))
            Text("保存名稱與資料夾，關閉視窗後也能從「專案紀錄」重新開啟。")
                .font(.system(size: 12)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            TextField("專案名稱", text: $name).textFieldStyle(.roundedBorder)
            if isAgent { AgentSessionFields(session: $session, tool: tool) }
            VStack(alignment: .leading, spacing: 7) {
                Text("專案資料夾").font(.system(size: 12, weight: .medium))
                HStack {
                    Text(directory.isEmpty ? "請選擇此工作的專案資料夾" : directory)
                        .font(.system(size: 11, design: .monospaced)).lineLimit(3).frame(maxWidth: .infinity, alignment: .leading)
                    Button("選擇…") {
                        let panel = NSOpenPanel(); panel.canChooseFiles = false; panel.canChooseDirectories = true; panel.allowsMultipleSelection = false
                        panel.directoryURL = URL(fileURLWithPath: directory.isEmpty ? model.preferences.lastDirectory : directory)
                        if panel.runModal() == .OK, let url = panel.url { directory = url.path }
                    }
                }
            }
            Toggle("固定此專案", isOn: $pinned).font(.system(size: 12))
            Text(isAgent ? "會記住預選方式，重開時仍可選新對話或續接。" : "重開時會在此資料夾開啟 Terminal。")
                .font(.system(size: 11)).foregroundStyle(.secondary)
            if let error = model.error {
                Text(error).font(.system(size: 11)).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Button("取消") { model.projectToSave = nil }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("儲存專案") { model.saveProject(row, directory: directory, name: name, pinned: pinned, session: isAgent ? session : nil) }
                    .keyboardShortcut(.defaultAction).buttonStyle(.borderedProminent).tint(.mint).foregroundStyle(.black)
                    .disabled(directory.isEmpty || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || (isAgent && !session.isValid))
            }
        }.padding(24).frame(width: 365).onAppear {
            name = row.title; directory = model.directory(for: row); pinned = model.project(for: row)?.pinned ?? true
            session = model.project(for: row)?.session ?? AgentSession(mode: .picker)
        }
    }
}

struct AgentSessionFields: View {
    @Binding var session: AgentSession
    let tool: String
    var expanded = false
    private var toolName: String { tool == "claude" ? "Claude" : "Codex" }
    private var modes: some View {
        Picker("對話啟動方式", selection: $session.mode) {
            ForEach(AgentStartMode.allCases, id: \.self) { mode in Text(mode.title).tag(mode) }
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if expanded { modes.pickerStyle(.radioGroup).labelsHidden() }
            else { modes }
            if session.mode == .specific {
                TextField("Session ID 或對話名稱", text: $session.sessionID).textFieldStyle(.roundedBorder)
            }
            if session.mode == .picker {
                if tool == "codex" { Toggle("包含其他資料夾的對話", isOn: $session.allProjects) }
                Text(tool == "codex" && !session.allProjects
                     ? "開啟 Terminal 後，從所選專案資料夾的 Codex 舊對話中選擇。"
                     : "開啟 Terminal 後，從 \(toolName) 清單選擇要接回的對話。")
                    .foregroundStyle(.secondary)
            } else if session.mode == .latest {
                Text("接回選定資料夾最近的對話；每次開啟時重新判定。")
                    .foregroundStyle(.secondary)
            } else if session.mode == .specific {
                Text("固定接回同一段對話，可貼上 \(toolName) 提供的 session ID 或對話名稱。")
                    .foregroundStyle(.secondary)
            } else {
                Text("建立一段新的 \(toolName) 對話。").foregroundStyle(.secondary)
            }
        }.font(.system(size: 11)).fixedSize(horizontal: false, vertical: true)
    }
}

struct OpenProjectView: View {
    @ObservedObject var model: PilotModel
    let project: SavedProject
    @State private var session: AgentSession

    init(model: PilotModel, project: SavedProject) {
        self.model = model
        self.project = project
        _session = State(initialValue: project.session ?? AgentSession(mode: .picker))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("開啟專案").font(.system(size: 21, weight: .semibold))
            VStack(alignment: .leading, spacing: 5) {
                Text(project.name).font(.system(size: 14, weight: .medium)).lineLimit(2)
                Text("\(project.toolName) · \(project.directory)")
                    .font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(3)
            }
            Text("這次要建立新對話，還是續接舊對話？").font(.system(size: 13))
            AgentSessionFields(session: $session, tool: project.tool, expanded: true).disabled(model.acting)
            Text("會記住這次選擇，下次開啟仍可更改。")
                .font(.system(size: 11)).foregroundStyle(.secondary)
            if let error = model.error {
                Text(error).font(.system(size: 11)).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Button("取消") { model.projectToOpen = nil }.keyboardShortcut(.cancelAction).disabled(model.acting)
                Spacer()
                Button(model.acting ? "開啟中…" : session.mode == .new ? "開啟新對話" : session.mode == .picker ? "開啟對話清單" : "續接對話") {
                    model.openProject(project, session: session)
                }.keyboardShortcut(.defaultAction).buttonStyle(.borderedProminent).tint(.mint).foregroundStyle(.black)
                    .disabled(model.acting || !session.isValid)
            }
        }.padding(24).frame(width: 400).interactiveDismissDisabled(model.acting)
    }
}

struct AgentProjectSettingsView: View {
    @ObservedObject var model: PilotModel
    let project: SavedProject
    @State private var session = AgentSession()
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("對話啟動方式").font(.system(size: 21, weight: .semibold))
            Text(project.name).font(.system(size: 13, weight: .medium))
            AgentSessionFields(session: $session, tool: project.tool)
            Text("儲存後，開啟專案時會預選此方式，當次仍可更改。續接仍保留完成通知。")
                .font(.system(size: 11)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack {
                Button("取消") { model.projectToConfigure = nil }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("儲存") { model.configureProject(project, session: session) }
                    .keyboardShortcut(.defaultAction).buttonStyle(.borderedProminent).tint(.mint).foregroundStyle(.black)
                    .disabled(!session.isValid)
            }
        }.padding(24).frame(width: 365).onAppear { session = project.session ?? AgentSession() }
    }
}

struct HelpView: View {
    @ObservedObject var model: PilotModel
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("使用 Terminal Pilot").font(.system(size: 21, weight: .semibold))
            Text("1. 視窗管理").font(.headline)
            Text("現有 Terminal 視窗會自動列出。雙擊項目或按「叫回」，就會還原視窗並移到最前面。勾選兩個視窗後按「並排」，再按「還原排列」。自由拖動的位置會隨 Terminal 保留。")
            Text("2. 需要你時才提醒").font(.headline)
            Text("完成待查看、等待確認時會亮紅點。送出回覆或 AI 恢復工作會清除；只輸入草稿或切換 Terminal 不會清除。也可雙擊工作、按「叫回」或「已處理」清除當下提醒。新的通知會再次提醒。")
            Text("新開的 Codex 工作自動接通手機核准與執行狀態。舊工作可在手機選擇保留對話並續接。Claude 使用本次工作附加的 hooks。")
                .foregroundStyle(.secondary)
            Text("3. 排序與專案紀錄").font(.headline)
            Text("待查看的工作自動排前面，最新通知在最上方；已讀後回到一般活動排序。新工作會保存到「專案紀錄」，可固定、改名與重新開啟。既有視窗可從 … 儲存為專案。Codex 與 Claude 專案每次開啟都可選新對話、挑選舊對話、續接最近或指定對話；上次選擇會保留為預選。")
            Text("既有 AI session 只提供視窗控制。要接上可靠通知，請開新的受管工作。完成表示本輪回覆結束，不等於程式已驗收。")
                .foregroundStyle(.secondary)
            HStack {
                Button("10 秒通知測試") {
                    model.showHelp = false
                    model.newTask(tool: "demo", directory: NSHomeDirectory(), label: "通知測試")
                }
                Spacer()
                Button("知道了") { model.showHelp = false }.keyboardShortcut(.defaultAction)
            }
        }.font(.system(size: 12)).padding(24).frame(width: 390)
    }
}
