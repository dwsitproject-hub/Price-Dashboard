(function () {
    // When a tab content div transitions from display:none to visible, Plotly charts
    // inside it have zero dimensions and won't render until the browser resizes.
    // This observer fires a synthetic resize event the moment each div becomes
    // visible so Plotly redraws immediately without requiring a manual resize.

    function triggerResize() {
        window.dispatchEvent(new Event('resize'));
    }

    function observeTabDiv(id) {
        var el = document.getElementById(id);
        if (!el) return false;
        var observer = new MutationObserver(function (mutations) {
            for (var i = 0; i < mutations.length; i++) {
                if (mutations[i].attributeName === 'style') {
                    if (el.style.display && el.style.display !== 'none') {
                        // Small delay so Dash finishes updating the DOM first
                        setTimeout(triggerResize, 50);
                    }
                }
            }
        });
        observer.observe(el, { attributes: true, attributeFilter: ['style'] });
        return true;
    }

    function setup() {
        var taOk  = observeTabDiv('ta-content');
        var oiOk  = observeTabDiv('oi-content');
        if (!taOk || !oiOk) {
            // Dash hasn't rendered the divs yet — retry shortly
            setTimeout(setup, 200);
        }
    }

    // Wait for Dash/React to finish its initial render before attaching observers
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { setTimeout(setup, 300); });
    } else {
        setTimeout(setup, 300);
    }
}());
