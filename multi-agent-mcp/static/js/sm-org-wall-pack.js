/**
 * Org-wall tile grid packing (same algorithm as APM Status Wall).
 * Used by /statusmonitor/production engineering mosaic.
 */
(function () {
    'use strict';

    function swCssWidthToPx(cssLen, contextEl) {
        var probe = document.createElement('div');
        probe.setAttribute(
            'style',
            'position:absolute;left:-9999px;top:0;width:' +
                cssLen +
                ';height:0;overflow:hidden;visibility:hidden;pointer-events:none'
        );
        (contextEl && contextEl.parentNode ? contextEl.parentNode : document.body).appendChild(probe);
        var px = probe.getBoundingClientRect().width;
        probe.remove();
        return px || 1;
    }

    function swCountWrappedLines(text, widthPx) {
        var probe = document.createElement('div');
        probe.style.cssText =
            'position:absolute;left:-9999px;top:0;visibility:hidden;white-space:normal;' +
            'overflow-wrap:anywhere;word-break:break-word;width:' +
            Math.max(32, widthPx) +
            'px;font-size:13px;line-height:1.22;font-weight:700;font-family:inherit;';
        probe.textContent = text || '';
        document.body.appendChild(probe);
        var lh = parseFloat(getComputedStyle(probe).lineHeight) || 16;
        var lines = Math.max(1, Math.ceil(probe.scrollHeight / lh));
        probe.remove();
        return Math.min(4, lines);
    }

    function swMeasureOrgWallTileSpans(grid, colCount) {
        if (!colCount || colCount < 1) return;
        var innerW = Math.max(48, grid.clientWidth / colCount - 16);
        var wraps = grid.querySelectorAll(':scope > .sw-tile-wrap');
        for (var i = 0; i < wraps.length; i++) {
            var wrap = wraps[i];
            var nameScroll = wrap.querySelector('.sw-tile-name-scroll');
            if (!nameScroll) continue;
            var lines = swCountWrappedLines(nameScroll.textContent || '', innerW);
            var hasChips = !!wrap.querySelector('.sw-tile-org-chips');
            var isAlert =
                wrap.classList.contains('sw-tile-wrap--size-warn') ||
                wrap.classList.contains('sw-tile-wrap--size-warn-wide') ||
                wrap.classList.contains('sw-tile-wrap--size-crit') ||
                wrap.classList.contains('sw-tile-wrap--size-crit-wide');
            var footRows = hasChips ? (isAlert ? 1 : 2) : 1;
            var nameRows = Math.max(1, lines);
            var rowSpan = nameRows + footRows;
            if (isAlert) {
                rowSpan = Math.max(rowSpan + 1, Math.ceil(rowSpan * 1.4));
            }
            wrap.setAttribute('data-org-row-span', String(rowSpan));
            wrap.setAttribute('data-org-text-lines', String(lines));
        }
    }

    function swPackTilesGrid(grid) {
        var wraps = Array.prototype.slice.call(grid.querySelectorAll(':scope > .sw-tile-wrap'));
        if (!wraps.length) {
            grid.style.gridTemplateColumns = '';
            grid.style.gridAutoFlow = '';
            grid.style.gridAutoRows = '';
            return;
        }
        var orgWall = grid.classList.contains('sw-tiles--org-wall');
        var w = grid.clientWidth;
        if (w < 2) return;

        function tileSpans(wrap, colCount) {
            if (orgWall) {
                var rs = parseInt(wrap.getAttribute('data-org-row-span') || '', 10);
                return { rowSpan: rs > 0 ? rs : 4, colSpan: 1 };
            }
            var rowSpan = 1;
            if (
                wrap.classList.contains('sw-tile-wrap--size-crit') ||
                wrap.classList.contains('sw-tile-wrap--size-crit-wide') ||
                wrap.classList.contains('sw-tile-wrap--size-warn') ||
                wrap.classList.contains('sw-tile-wrap--size-warn-wide')
            ) {
                rowSpan = 2;
            }
            var colSpan = 1;
            if (wrap.classList.contains('sw-tile-wrap--span-3')) {
                colSpan = 3;
            } else if (
                wrap.classList.contains('sw-tile-wrap--size-ok-wide') ||
                wrap.classList.contains('sw-tile-wrap--size-warn') ||
                wrap.classList.contains('sw-tile-wrap--size-warn-wide') ||
                wrap.classList.contains('sw-tile-wrap--size-crit') ||
                wrap.classList.contains('sw-tile-wrap--size-crit-wide')
            ) {
                colSpan = 2;
            }
            if (colSpan > colCount) colSpan = colCount;
            return { rowSpan: rowSpan, colSpan: colSpan };
        }

        function packWithColCount(colCount) {
            grid.style.gridTemplateColumns = 'repeat(' + colCount + ', minmax(0, 1fr))';
            grid.style.gridAutoFlow = 'row';
            grid.style.gridAutoRows = orgWall ? 'var(--sw-tile-row-unit, 1.52rem)' : '';

            var occ = Object.create(null);
            var maxColUsed = 0;
            function occKey(r, c) {
                return r + ',' + c;
            }
            function canPlace(r, c, rowSpan, colSpan) {
                if (c + colSpan > colCount) return false;
                for (var dr = 0; dr < rowSpan; dr++) {
                    for (var dc = 0; dc < colSpan; dc++) {
                        if (occ[occKey(r + dr, c + dc)]) return false;
                    }
                }
                return true;
            }
            function markOcc(r, c, rowSpan, colSpan) {
                for (var dr = 0; dr < rowSpan; dr++) {
                    for (var dc = 0; dc < colSpan; dc++) {
                        occ[occKey(r + dr, c + dc)] = 1;
                    }
                }
            }

            for (var i = 0; i < wraps.length; i++) {
                var wrap = wraps[i];
                wrap.style.gridRow = '';
                wrap.style.gridColumn = '';
                wrap.style.removeProperty('max-width');
                var sp = tileSpans(wrap, colCount);
                var placed = false;
                for (var r = 0; r < 900 && !placed; r++) {
                    for (var c = 0; c <= colCount - sp.colSpan; c++) {
                        if (canPlace(r, c, sp.rowSpan, sp.colSpan)) {
                            wrap.style.gridRow = r + 1 + ' / span ' + sp.rowSpan;
                            wrap.style.gridColumn = c + 1 + ' / span ' + sp.colSpan;
                            markOcc(r, c, sp.rowSpan, sp.colSpan);
                            maxColUsed = Math.max(maxColUsed, c + sp.colSpan);
                            placed = true;
                            break;
                        }
                    }
                }
            }
            return maxColUsed;
        }

        var cs = getComputedStyle(grid);
        var colMinStr = (cs.getPropertyValue('--sw-tile-col-min') || '').trim();
        if (!colMinStr) colMinStr = '7.75rem';
        var colMinPx = swCssWidthToPx(colMinStr, grid);
        var colCount = Math.max(1, Math.floor(w / colMinPx));
        var minCols = parseInt(grid.getAttribute('data-tile-cols') || '0', 10);
        if (minCols > 0) {
            colCount = Math.max(colCount, minCols);
        }

        function runPack(cols) {
            if (orgWall) {
                swMeasureOrgWallTileSpans(grid, cols);
                wraps.sort(function (a, b) {
                    var ra = parseInt(a.getAttribute('data-org-row-span') || '4', 10);
                    var rb = parseInt(b.getAttribute('data-org-row-span') || '4', 10);
                    return rb - ra;
                });
            }
            return packWithColCount(cols);
        }

        var maxColUsed = runPack(colCount);
        if (orgWall && maxColUsed > 0 && maxColUsed < colCount) {
            colCount = maxColUsed;
            maxColUsed = runPack(colCount);
        }
        if (orgWall && maxColUsed > 0) {
            grid.style.gridTemplateColumns = 'repeat(' + maxColUsed + ', minmax(0, 1fr))';
        }
    }

    var _smOrgWallPackT = null;

    function smOrgWallSchedulePack(root) {
        clearTimeout(_smOrgWallPackT);
        _smOrgWallPackT = setTimeout(function () {
            _smOrgWallPackT = null;
            var scope = root && root.querySelectorAll ? root : document;
            var nodes =
                scope === document
                    ? document.querySelectorAll('.sm-eng-mosaic-wrap .sw-tiles.sw-tiles--org-wall')
                    : scope.querySelectorAll('.sw-tiles.sw-tiles--org-wall');
            nodes.forEach(function (el) {
                swPackTilesGrid(el);
            });
        }, 32);
    }

    function smOrgWallInitPackResize() {
        if (window._smOrgWallPackResizeInit) return;
        window._smOrgWallPackResizeInit = true;
        var root = document.getElementById('dashboard') || document.body;
        if (typeof ResizeObserver === 'undefined') {
            window.addEventListener('resize', function () {
                smOrgWallSchedulePack(document.getElementById('dashboard'));
            });
            return;
        }
        var roT = null;
        var ro = new ResizeObserver(function () {
            clearTimeout(roT);
            roT = setTimeout(function () {
                smOrgWallSchedulePack(document.getElementById('dashboard'));
            }, 48);
        });
        ro.observe(root);
    }

    window.smOrgWallPackGrid = swPackTilesGrid;
    window.smOrgWallSchedulePack = smOrgWallSchedulePack;
    window.smOrgWallInitPackResize = smOrgWallInitPackResize;
})();
