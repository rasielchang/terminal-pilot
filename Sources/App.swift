import AppKit
import SwiftUI

final class PilotHostingView<Content: View>: NSHostingView<Content> {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

@MainActor final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    var window: NSWindow!
    var statusItem: NSStatusItem!
    let model = PilotModel()
    func applicationDidFinishLaunching(_ notification: Notification) {
        if let resource = Bundle.main.resourceURL,
           let icon = NSImage(contentsOf: resource.appendingPathComponent("Pilot.icns")) {
            NSApp.applicationIconImage = icon
        }
        let mainMenu = NSMenu()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "顯示 Terminal Pilot", action: #selector(show), keyEquivalent: "0").target = self
        appMenu.addItem(withTitle: "環境設定…", action: #selector(setup), keyEquivalent: ",").target = self
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "結束 Terminal Pilot", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let root = NSMenuItem(); root.submenu = appMenu; mainMenu.addItem(root)
        let edit = NSMenuItem(title: "編輯", action: nil, keyEquivalent: "")
        let editMenu = NSMenu(title: "編輯")
        editMenu.addItem(withTitle: "剪下", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "複製", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "貼上", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "全選", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        edit.submenu = editMenu; mainMenu.addItem(edit); NSApp.mainMenu = mainMenu
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 400, height: 740),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Terminal Pilot"
        window.identifier = NSUserInterfaceItemIdentifier("pilot")
        window.titlebarAppearsTransparent = true
        window.backgroundColor = NSColor(red: 0.075, green: 0.086, blue: 0.103, alpha: 1)
        window.appearance = NSAppearance(named: .darkAqua)
        window.contentView = PilotHostingView(rootView: PilotView(model: model))
        window.minSize = NSSize(width: 370, height: 520)
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.setFrameAutosaveName("TerminalPilotWindow")
        if !window.setFrameUsingName("TerminalPilotWindow"), let screen = NSScreen.main {
            let rect = screen.visibleFrame
            window.setFrame(NSRect(x: rect.maxX - 410, y: rect.maxY - min(780, rect.height) + 4,
                                   width: 400, height: min(770, rect.height - 8)), display: false)
        }
        pin(model.pinned)
        model.onPin = { [weak self] in self?.pin($0) }
        model.onCount = { [weak self] count in
            self?.statusItem.button?.title = count > 0 ? " \(count)" : ""
            self?.statusItem.button?.contentTintColor = count > 0 ? .systemRed : nil
            NSApp.dockTile.badgeLabel = count > 0 ? String(count) : nil
        }
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.image = NSImage(systemSymbolName: "rectangle.stack", accessibilityDescription: "Terminal Pilot")
        let menu = NSMenu()
        menu.addItem(withTitle: "顯示工作視窗", action: #selector(show), keyEquivalent: "").target = self
        menu.addItem(withTitle: "新增工作", action: #selector(newTask), keyEquivalent: "").target = self
        menu.addItem(withTitle: "本地工作網頁", action: #selector(web), keyEquivalent: "").target = self
        menu.addItem(withTitle: "環境設定…", action: #selector(setup), keyEquivalent: "").target = self
        menu.addItem(withTitle: "使用方式 / 通知測試", action: #selector(help), keyEquivalent: "").target = self
        menu.addItem(.separator())
        menu.addItem(withTitle: "結束", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "")
        statusItem.menu = menu
        show(); model.start()
        if !FileManager.default.fileExists(atPath: model.store.appendingPathComponent("preferences.json").path) {
            model.showSetup = true
        }
    }
    func pin(_ enabled: Bool) {
        window.level = enabled ? .floating : .normal
        window.collectionBehavior = enabled ? [.canJoinAllSpaces, .fullScreenAuxiliary] : [.managed]
    }
    @objc func show() { window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true) }
    @objc func newTask() { show(); model.showNew = true }
    @objc func help() { show(); model.showHelp = true }
    @objc func setup() { show(); model.showSetup = true }
    @objc func web() { show(); model.showWebSettings() }
    func applicationWillTerminate(_ notification: Notification) { model.stopWeb(remember: false) }
    func windowShouldClose(_ sender: NSWindow) -> Bool { sender.orderOut(nil); return false }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { show(); return true }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
}

@main enum TerminalPilot {
    @MainActor static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        withExtendedLifetime(delegate) { app.run() }
    }
}
