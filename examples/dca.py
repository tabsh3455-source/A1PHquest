"""DCA strategy template for A1phquest."""

from vnpy_ctastrategy import CtaTemplate


class DcaStrategy(CtaTemplate):
    author = "A1phquest Team"
    buy_interval_minutes = 60
    order_volume = 0.01

    parameters = ["buy_interval_minutes", "order_volume"]
    variables = []

    def on_init(self):
        self.write_log("DCA strategy initialized")

    def on_start(self):
        self.write_log("DCA strategy started")

    def on_stop(self):
        self.write_log("DCA strategy stopped")

    def on_bar(self, bar):
        # Implement periodic buy logic in production.
        pass

