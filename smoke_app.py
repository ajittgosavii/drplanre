"""Headless smoke test of every scenario x design with streamlit AppTest."""
from streamlit.testing.v1 import AppTest
import scenarios as S
for o in (1, 2):
    for name in S.SCENARIOS:
        at = AppTest.from_file("app.py", default_timeout=60)
        at.run()
        at.sidebar.radio[0].set_value(o).run()
        at.sidebar.selectbox[0].set_value(name).run()
        for _ in range(3):
            at.button[2].click().run()
        assert not at.exception, (o, name, at.exception)
        at.button[3].click().run()
        assert not at.exception, (o, name, at.exception)
        print("ok", o, name)
