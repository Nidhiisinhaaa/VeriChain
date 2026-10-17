/* Drag-and-drop feedback for file inputs + tab switching. No dependencies. */
(function () {
    function initDropzone(dropzone) {
        var input = dropzone.querySelector('input[type="file"]');
        var filenameEl = dropzone.querySelector('.dropzone-filename');
        if (!input) return;

        function showFilename() {
            if (input.files && input.files.length > 0) {
                filenameEl.textContent = 'Selected: ' + input.files[0].name;
            } else {
                filenameEl.textContent = '';
            }
        }

        input.addEventListener('change', showFilename);

        ['dragenter', 'dragover'].forEach(function (evt) {
            dropzone.addEventListener(evt, function (e) {
                e.preventDefault();
                e.stopPropagation();
                dropzone.classList.add('dragover');
            });
        });

        ['dragleave', 'drop'].forEach(function (evt) {
            dropzone.addEventListener(evt, function (e) {
                e.preventDefault();
                e.stopPropagation();
                dropzone.classList.remove('dragover');
            });
        });

        dropzone.addEventListener('drop', function (e) {
            var files = e.dataTransfer.files;
            if (files && files.length > 0) {
                input.files = files;
                showFilename();
            }
        });
    }

    function initTabs() {
        var tabButtons = document.querySelectorAll('.tab-button');
        if (!tabButtons.length) return;

        tabButtons.forEach(function (button) {
            button.addEventListener('click', function () {
                var target = button.getAttribute('data-tab');

                tabButtons.forEach(function (b) { b.classList.remove('active'); });
                button.classList.add('active');

                document.querySelectorAll('.tab-panel').forEach(function (panel) {
                    panel.classList.toggle('active', panel.getAttribute('data-tab-panel') === target);
                });
            });
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('.dropzone').forEach(initDropzone);
        initTabs();
    });
})();
