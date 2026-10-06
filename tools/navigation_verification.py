"""Navigation checks against the actual static release in a disposable browser."""

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys


def open_tool(check, name):
    driver = check.d
    check.wait.until(lambda _: driver.find_elements(By.CSS_SELECTOR, f'[data-tab="{name}"]'))
    link = driver.find_element(By.CSS_SELECTOR, f'[data-tab="{name}"]')
    if not link.is_displayed():
        driver.find_element(By.ID, "tools-toggle").click()
        check.wait.until(lambda _: link.is_displayed())
    link.click()
    check.wait.until(
        lambda _: driver.find_element(By.ID, "workspace").get_attribute("aria-busy") != "true"
    )
    assert not driver.find_element(By.ID, "navigation-retry").is_displayed(), driver.find_element(
        By.ID, "navigation-message"
    ).text


def verify(check):
    d, wait = check.d, check.wait

    def el(name):
        return d.find_element(By.ID, name)

    wait.until(lambda _: len(d.find_elements(By.CSS_SELECTOR, "#tool-navigation a")) == 8)
    assert el("home-tool").is_displayed()
    assert len(d.find_elements(By.CSS_SELECTOR, "#home-cards .tool-card")) == 7
    assert not d.find_elements(By.CSS_SELECTOR, '[role="tablist"]')
    assert not any("/assets/" in item["url"] for item in check.requests)
    check.check(
        "Home opens with seven grouped cards and no engine, image, model or reference asset downloads"
    )
    d.save_screenshot(str(check.out / "home-desktop.png"))
    routes = {
        "package": "tattoos",
        "hair": "hair",
        "object": "objects",
        "painting": "paintings",
        "sim": "sim",
        "texture": "convert",
        "upscale": "upscale",
        "home": "home",
    }
    for name, route in routes.items():
        open_tool(check, name)
        assert d.current_url.endswith("#/" + route)
        assert (
            d.find_element(By.CSS_SELECTOR, '#tool-navigation [aria-current="page"]').get_attribute(
                "data-tab"
            )
            == name
        )
        assert (
            check.page(
                "return document.querySelectorAll('#workspace > .tool-panel:not([hidden])').length;"
            )
            == 1
        )
        assert check.page("return !!document.activeElement.closest('#workspace');")
    check.check("All routes activate one labelled workspace and focus its heading")
    open_tool(check, "object")
    el("object-title").send_keys("Navigation draft")
    open_tool(check, "home")
    d.back()
    wait.until(lambda _: el("object-tool").is_displayed())
    assert el("object-title").get_attribute("value") == "Navigation draft"
    d.forward()
    wait.until(lambda _: el("home-tool").is_displayed())
    d.get(check.url + "#/upscale")
    wait.until(lambda _: el("upscale-tool").is_displayed())
    d.refresh()
    wait.until(lambda _: el("upscale-tool").is_displayed())
    d.get(check.url + "#/missing")
    wait.until(lambda _: el("navigation-notice").is_displayed())
    assert d.current_url.endswith("#/home")
    check.check(
        "Back/Forward preserves edits, refresh restores deep links, and invalid routes explain the Home fallback"
    )
    for width in [1199, 768, 390, 320]:
        d.set_window_size(width, 900)
        assert el("tools-toggle").is_displayed()
        el("tools-toggle").click()
        wait.until(lambda _: el("tool-drawer").get_attribute("open") is not None)
        for _ in range(12):
            d.switch_to.active_element.send_keys(Keys.TAB)
            assert check.page("return !!document.activeElement.closest('#tool-drawer');")
        d.switch_to.active_element.send_keys(Keys.ESCAPE)
        wait.until(lambda _: not el("tool-drawer").is_displayed())
        assert d.switch_to.active_element == el("tools-toggle")
        open_tool(check, "hair")
        assert not el("tool-drawer").is_displayed()
        assert check.page("return document.documentElement.scrollWidth <= innerWidth + 1;")
        open_tool(check, "home")
    d.save_screenshot(str(check.out / "home-mobile.png"))
    el("tools-toggle").click()
    d.save_screenshot(str(check.out / "tools-mobile.png"))
    d.set_window_size(1280, 1000)
    wait.until(lambda _: not el("tool-drawer").is_displayed())
    assert d.find_element(By.CSS_SELECTOR, '#tool-navigation [aria-current="page"]').is_displayed()
    check.check(
        "Responsive drawer traps focus, closes with Escape or selection, restores focus and survives breakpoint changes"
    )
    # Keyboard skip navigation must not be interpreted as an unknown tool route.
    check.page("document.querySelector('.skip-link').focus();")
    d.switch_to.active_element.send_keys(Keys.ENTER)
    assert d.switch_to.active_element == el("workspace")
    assert not el("navigation-notice").is_displayed()
    open_tool(check, "texture")
