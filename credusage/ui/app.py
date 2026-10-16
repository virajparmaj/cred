"""Native status item and transient usage popover; collection stays asynchronous."""

from __future__ import annotations

import logging
import sys

from credusage.ui.dashboard import ResetRefreshTracker, build_dashboard

LOG = logging.getLogger(__name__)
_native_delegate = None


def delegate_class():
    global _native_delegate
    if _native_delegate is not None:
        return _native_delegate
    import AppKit as A
    import objc

    class CredUsageApplicationDelegate(A.NSObject):
        owner = objc.ivar()

        def toggle_(self, sender):
            self.owner.toggle()

        def tick_(self, timer):
            self.owner.tick()

        def performAction_(self, sender):
            callback = self.owner.usage.callbacks.get(int(sender.tag()))
            if callback:
                callback()

        def contextChanged_(self, sender):
            self.owner.usage.context_changed(sender)

        def poolChanged_(self, sender):
            self.owner.usage.pool_changed(sender)

        def woke_(self, notification):
            self.owner.controller.refresh(reason="wake")

        def popoverDidClose_(self, notification):
            self.owner.usage.provider_id = None
            self.owner.usage.context_id = None

        def applicationShouldTerminateAfterLastWindowClosed_(self, application):
            return False

        def applicationWillTerminate_(self, notification):
            self.owner.shutdown()

        def userNotificationCenter_shouldPresentNotification_(self, center, notification):
            return True

    _native_delegate = CredUsageApplicationDelegate
    return _native_delegate


class NativeShell:
    """Own native objects strongly for their target/action and observer lifetimes."""

    def __init__(self, controller):
        import AppKit as A

        from credusage.ui.popover import ASSETS, UsagePopover

        self.A = A
        self.controller = controller
        self.closed = False
        self.resets = ResetRefreshTracker()
        self.application = A.NSApplication.sharedApplication()
        self.application.setActivationPolicy_(A.NSApplicationActivationPolicyAccessory)
        self.delegate = delegate_class().alloc().init()
        self.delegate.owner = self
        self.application.setDelegate_(self.delegate)
        self.status_item = A.NSStatusBar.systemStatusBar().statusItemWithLength_(
            A.NSSquareStatusItemLength
        )
        button = self.status_item.button()
        image = A.NSImage.alloc().initWithSize_((18, 18))
        for name in ("MenuIconTemplate.png", "MenuIconTemplate@2x.png"):
            representation = A.NSBitmapImageRep.imageRepWithContentsOfFile_(str(ASSETS / name))
            representation.setSize_((18, 18))
            image.addRepresentation_(representation)
        image.setTemplate_(True)
        button.setImage_(image)
        button.setTitle_("")
        button.setToolTip_(
            "CredUsage · AI usage and reset times" + (" · Demo" if controller.config.demo else "")
        )
        button.setAccessibilityLabel_(
            "CredUsage usage menu" + (" · Demo" if controller.config.demo else "")
        )
        button.setTarget_(self.delegate)
        button.setAction_("toggle:")
        self.usage = UsagePopover(self)
        A.NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
            self.delegate, "woke:", A.NSWorkspaceDidWakeNotification, None
        )
        self.timer = A.NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            1, self.delegate, "tick:", None, True
        )
        A.NSRunLoop.mainRunLoop().addTimer_forMode_(self.timer, A.NSRunLoopCommonModes)
        try:
            A.NSUserNotificationCenter.defaultUserNotificationCenter().setDelegate_(self.delegate)
        except (AttributeError, RuntimeError):
            LOG.info("Notification center is unavailable in this launch context")
        self.tick()
        controller.refresh(reason="startup")

    def tick(self):
        try:
            states = self.controller.states()
            now = self.controller.clock.now()
            for connection_id in self.resets.due(states, now):
                self.controller.refresh(connection_id, reason="reset")
            dashboard = build_dashboard(
                states,
                self.controller.registry.descriptors(),
                now,
                self.controller.config.pinned_connection_id,
                self.controller.config.demo,
            )
            self.usage.update(dashboard)
            for title, subtitle, message in self.controller.drain_notifications():
                try:
                    import rumps

                    rumps.notification(title, subtitle, message)
                except Exception:
                    LOG.warning("macOS declined a notification; usage remains visible")
        except Exception:
            LOG.exception("Usage popover could not be refreshed")

    def toggle(self):
        if self.usage.popover.isShown():
            self.usage.close()
            return
        self.controller.refresh(reason="menu")
        self.tick()
        self.usage.overview()
        self.application.activateIgnoringOtherApps_(True)
        button = self.status_item.button()
        self.usage.popover.showRelativeToRect_ofView_preferredEdge_(
            button.bounds(), button, self.A.NSMinYEdge
        )
        if self.usage.focusables:
            self.usage.controller.view().window().makeFirstResponder_(self.usage.focusables[0])

    def settings(self):
        from credusage.ui.settings import show_settings

        self.usage.close()
        show_settings(self.controller, self.tick)

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        self.timer.invalidate()
        self.usage.close()
        self.A.NSWorkspace.sharedWorkspace().notificationCenter().removeObserver_(self.delegate)
        self.A.NSStatusBar.systemStatusBar().removeStatusItem_(self.status_item)
        self.controller.close()

    def quit(self):
        self.shutdown()
        self.application.terminate_(None)


def run_app(controller) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("The native CredUsage menu bar requires macOS.")
    shell = NativeShell(controller)
    try:
        shell.application.run()
    finally:
        shell.shutdown()
