/*
 * Liste déroulante personnalisée (UPB) : remplace un <select> natif tout en le conservant dans la page.
 *
 *  - Le <select> d'origine reste la source de vérité : `select.value`, `change`, `form.reset()` et les
 *    lectures existantes continuent de fonctionner. L'interface se met à jour toute seule quand le code
 *    modifie `select.value`.
 *  - Une valeur absente de la liste (donnée historique) est ajoutée en fin de liste avec la mention
 *    « (hors liste) » pour ne jamais être effacée par mégarde.
 *  - Accessible : rôles combobox/listbox/option, flèches, Début/Fin, Entrée, Espace, Échap, saisie de la
 *    première lettre.
 *
 * Utilisation :
 *   <select id="x" data-upb-select data-placeholder="Choisir…" data-empty-label="Non précisé"
 *           data-size="lg"></select>            (data-size="lg" : champs plus grands du portail candidat)
 *   UPBSelect.setOptions(document.getElementById('x'), ['A', 'B']);
 */
(function () {
    'use strict';

    var STYLE = [
        '.upb-native{position:absolute!important;width:1px!important;height:1px!important;opacity:0!important;pointer-events:none!important;overflow:hidden!important;clip:rect(0 0 0 0)!important}',
        '.upb-select{position:relative;width:100%}',
        '.upb-trigger{display:flex;align-items:center;justify-content:space-between;gap:10px;width:100%;margin:0;text-align:left;',
        'padding:10px 12px;border:1.5px solid #dbe5f7;border-radius:11px;background:#fff;color:var(--navy,#1b2275);',
        'font:500 .9rem var(--font,system-ui,sans-serif);cursor:pointer;transition:border-color .2s,box-shadow .2s,background-color .2s}',
        '.upb-select[data-size=lg] .upb-trigger{padding:14px 16px;border-width:2px;border-color:#cfdcf3;border-radius:14px;font-size:1rem}',
        '.upb-trigger:hover{border-color:#b4c7ee}',
        '.upb-trigger:focus-visible,.upb-select.open .upb-trigger{outline:none;border-color:var(--royal,#1f55d8);box-shadow:0 0 0 4px rgba(31,85,216,.16)}',
        '.upb-value{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
        '.upb-value.placeholder{color:#93a3c6;font-weight:400}',
        '.upb-chevron{flex:none;width:18px;height:18px;color:var(--mut,#5b6a96);transition:transform .2s}',
        '.upb-select.open .upb-chevron{transform:rotate(180deg);color:var(--royal,#1f55d8)}',
        '.upb-list{position:fixed;z-index:2147483000;box-sizing:border-box;margin:0;padding:6px;list-style:none;background:#fff;',
        'border:1.5px solid #dbe5f7;border-radius:14px;box-shadow:0 26px 50px -16px rgba(0,10,70,.5);max-height:264px;overflow-y:auto;',
        'animation:upbpop .14s ease-out}',
        '@keyframes upbpop{from{opacity:0;transform:translateY(-4px) scale(.98)}to{opacity:1;transform:none}}',
        '.upb-opt{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 12px;border-radius:10px;cursor:pointer;',
        'color:var(--navy,#1b2275);font:600 .9rem var(--font,system-ui,sans-serif);user-select:none}',
        '.upb-select[data-size=lg]~.upb-list .upb-opt,.upb-list[data-size=lg] .upb-opt{padding:12px 14px;font-size:1rem}',
        '.upb-opt.active{background:#eef3ff}',
        '.upb-opt[aria-selected=true]{background:#e7eeff;color:var(--royal2,#173fa8);font-weight:800}',
        '.upb-opt.empty{color:#6b7aa6;font-weight:500}',
        '.upb-opt small{font-weight:500;color:#8a5200}',
        '.upb-opt svg{flex:none;width:16px;height:16px;color:var(--royal,#1f55d8);visibility:hidden}',
        '.upb-opt[aria-selected=true] svg{visibility:visible}',
        '@media (prefers-reduced-motion:reduce){.upb-list{animation:none}.upb-chevron{transition:none}}'
    ].join('');

    var CHEVRON = '<svg class="upb-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
    var CHECK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>';
    var valueDescriptor = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
    var counter = 0;
    var openInstance = null;

    function injectStyle() {
        if (document.getElementById('upb-select-style')) return;
        var style = document.createElement('style');
        style.id = 'upb-select-style';
        style.textContent = STYLE;
        document.head.appendChild(style);
    }

    function enhance(select) {
        if (select._upb) return select._upb;
        injectStyle();
        var id = 'upb-' + (++counter);
        var wrapper = document.createElement('div');
        wrapper.className = 'upb-select';
        if (select.dataset.size) wrapper.dataset.size = select.dataset.size;
        var trigger = document.createElement('button');
        trigger.type = 'button';
        trigger.className = 'upb-trigger';
        trigger.setAttribute('role', 'combobox');
        trigger.setAttribute('aria-haspopup', 'listbox');
        trigger.setAttribute('aria-expanded', 'false');
        trigger.setAttribute('aria-controls', id + '-list');
        var label = select.id ? document.querySelector('label[for="' + select.id + '"]') : null;
        if (label) trigger.setAttribute('aria-label', label.textContent.replace(/\s*[*(].*$/, '').trim());
        trigger.innerHTML = '<span class="upb-value"></span>' + CHEVRON;
        var list = document.createElement('ul');
        list.className = 'upb-list';
        list.id = id + '-list';
        list.setAttribute('role', 'listbox');
        if (select.dataset.size) list.dataset.size = select.dataset.size;

        select.classList.add('upb-native');
        select.tabIndex = -1;
        select.setAttribute('aria-hidden', 'true');
        select.parentNode.insertBefore(wrapper, select);
        wrapper.appendChild(select);
        wrapper.appendChild(trigger);

        var state = { active: -1, typed: '', typedTimer: null };

        function options() { return Array.prototype.slice.call(select.options); }

        function sync() {
            var chosen = select.selectedIndex >= 0 ? select.options[select.selectedIndex] : null;
            var text = chosen && chosen.value !== '' ? chosen.textContent : (select.dataset.placeholder || 'Choisir…');
            var holder = trigger.querySelector('.upb-value');
            holder.textContent = text;
            holder.classList.toggle('placeholder', !chosen || chosen.value === '');
        }

        function renderList() {
            list.innerHTML = '';
            options().forEach(function (option, index) {
                var item = document.createElement('li');
                item.className = 'upb-opt' + (option.value === '' ? ' empty' : '');
                item.id = id + '-opt-' + index;
                item.setAttribute('role', 'option');
                item.setAttribute('aria-selected', option.selected ? 'true' : 'false');
                item.dataset.index = index;
                var text = document.createElement('span');
                text.textContent = option.value === '' ? (select.dataset.emptyLabel || 'Non précisé') : option.dataset.legacy ? option.value : option.textContent;
                item.appendChild(text);
                if (option.dataset.legacy) {
                    var tag = document.createElement('small');
                    tag.textContent = '(hors liste)';
                    text.appendChild(document.createTextNode(' '));
                    text.appendChild(tag);
                }
                item.insertAdjacentHTML('beforeend', CHECK);
                list.appendChild(item);
            });
        }

        function setActive(index, scroll) {
            var items = list.children;
            if (!items.length) return;
            state.active = Math.max(0, Math.min(items.length - 1, index));
            Array.prototype.forEach.call(items, function (item, i) { item.classList.toggle('active', i === state.active); });
            trigger.setAttribute('aria-activedescendant', items[state.active].id);
            if (scroll !== false) items[state.active].scrollIntoView({ block: 'nearest' });
        }

        function place() {
            var box = trigger.getBoundingClientRect();
            var below = window.innerHeight - box.bottom - 12;
            var above = box.top - 12;
            var height = Math.min(264, list.scrollHeight + 14);
            var openUp = below < height && above > below;
            list.style.width = box.width + 'px';
            list.style.left = Math.max(8, Math.min(box.left, window.innerWidth - box.width - 8)) + 'px';
            list.style.maxHeight = Math.max(120, Math.min(264, openUp ? above : below)) + 'px';
            if (openUp) { list.style.top = 'auto'; list.style.bottom = (window.innerHeight - box.top + 6) + 'px'; }
            else { list.style.bottom = 'auto'; list.style.top = (box.bottom + 6) + 'px'; }
        }

        function open() {
            if (openInstance && openInstance !== api) openInstance.close();
            if (!select.options.length) return;
            renderList();
            (select.closest('dialog') || document.body).appendChild(list);
            wrapper.classList.add('open');
            trigger.setAttribute('aria-expanded', 'true');
            place();
            setActive(select.selectedIndex >= 0 ? select.selectedIndex : 0);
            openInstance = api;
        }

        function close(focus) {
            wrapper.classList.remove('open');
            trigger.setAttribute('aria-expanded', 'false');
            trigger.removeAttribute('aria-activedescendant');
            if (list.parentNode) list.parentNode.removeChild(list);
            if (openInstance === api) openInstance = null;
            if (focus) trigger.focus();
        }

        function choose(index) {
            var option = select.options[index];
            if (!option) return;
            var changed = select.selectedIndex !== index;
            valueDescriptor.set.call(select, option.value);
            sync();
            close(true);
            if (changed) select.dispatchEvent(new Event('change', { bubbles: true }));
        }

        trigger.addEventListener('click', function () { wrapper.classList.contains('open') ? close() : open(); });
        trigger.addEventListener('keydown', function (event) {
            var isOpen = wrapper.classList.contains('open');
            var key = event.key;
            if (key === 'ArrowDown' || key === 'ArrowUp') {
                event.preventDefault();
                if (!isOpen) { open(); return; }
                setActive(state.active + (key === 'ArrowDown' ? 1 : -1));
            } else if (key === 'Home' || key === 'End') {
                if (isOpen) { event.preventDefault(); setActive(key === 'Home' ? 0 : list.children.length - 1); }
            } else if (key === 'Enter' || key === ' ') {
                event.preventDefault();
                if (isOpen) choose(state.active); else open();
            } else if (key === 'Escape') {
                if (isOpen) { event.preventDefault(); event.stopPropagation(); close(true); }
            } else if (key === 'Tab') {
                if (isOpen) close();
            } else if (key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
                clearTimeout(state.typedTimer);
                state.typed += key.toLowerCase();
                state.typedTimer = setTimeout(function () { state.typed = ''; }, 650);
                var found = options().findIndex(function (o) { return o.value !== '' && o.textContent.toLowerCase().indexOf(state.typed) === 0; });
                if (found >= 0) { if (!isOpen) open(); setActive(found); }
            }
        });
        list.addEventListener('mousedown', function (event) { event.preventDefault(); });
        list.addEventListener('click', function (event) {
            var item = event.target.closest('.upb-opt');
            if (item) choose(Number(item.dataset.index));
        });
        list.addEventListener('mousemove', function (event) {
            var item = event.target.closest('.upb-opt');
            if (item && Number(item.dataset.index) !== state.active) setActive(Number(item.dataset.index), false);
        });
        if (label) label.addEventListener('click', function (event) { event.preventDefault(); trigger.focus(); });
        if (select.form) select.form.addEventListener('reset', function () { setTimeout(sync, 0); });

        // `select.value = x` (code existant) met l'interface à jour ; une valeur inconnue est conservée.
        Object.defineProperty(select, 'value', {
            configurable: true,
            get: function () { return valueDescriptor.get.call(select); },
            set: function (value) {
                value = value == null ? '' : String(value);
                if (value !== '' && !options().some(function (o) { return o.value === value; })) {
                    var legacy = document.createElement('option');
                    legacy.value = value;
                    legacy.textContent = value;
                    legacy.dataset.legacy = '1';
                    select.appendChild(legacy);
                }
                valueDescriptor.set.call(select, value);
                sync();
            }
        });

        var api = {
            sync: sync,
            close: close,
            contains: function (node) { return wrapper.contains(node) || list.contains(node); },
            reposition: function () { if (wrapper.classList.contains('open')) place(); }
        };
        select._upb = api;
        sync();
        return api;
    }

    /** Remplace les valeurs de la liste (la valeur courante est conservée si elle existe encore). */
    function setOptions(select, values) {
        enhance(select);
        var current = select.value;
        select.innerHTML = '';
        var empty = document.createElement('option');
        empty.value = '';
        empty.textContent = select.dataset.emptyLabel || 'Non précisé';
        select.appendChild(empty);
        values.forEach(function (value) {
            var option = document.createElement('option');
            option.value = value;
            option.textContent = value;
            select.appendChild(option);
        });
        select.value = current;  // un ancien choix hors liste redevient une entrée « (hors liste) »
        select._upb.sync();
    }

    function enhanceAll(root) {
        Array.prototype.forEach.call((root || document).querySelectorAll('select[data-upb-select]'), enhance);
    }

    document.addEventListener('pointerdown', function (event) {
        if (openInstance && !openInstance.contains(event.target)) openInstance.close();
    });
    window.addEventListener('resize', function () { if (openInstance) openInstance.reposition(); });
    window.addEventListener('scroll', function (event) {
        if (openInstance && !openInstance.contains(event.target)) openInstance.reposition();
    }, true);

    window.UPBSelect = { enhance: enhance, enhanceAll: enhanceAll, setOptions: setOptions };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { enhanceAll(); });
    else enhanceAll();
})();
