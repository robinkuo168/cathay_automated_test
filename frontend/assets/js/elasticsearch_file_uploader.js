let selectedFiles = [];
const API_BASE = '/api/es'; // Set API base for Elasticsearch endpoints

// Index configurations
const INDEX_CONFIGS = {
    documents: {
        indexName: 'cathay_project1_chunks',
        allowedTypes: ['.txt', '.xlsx', '.xls', '.yaml', '.yml'],
        displayTypes: 'TXT, XLSX, YAML',
        description: '將 TXT, XLSX, 和 YAML 檔案直接上傳至向量資料庫',
        buttonText: '上傳至向量資料庫'
    },
    agent: {
        indexName: 'my_agent_versions',
        allowedTypes: ['.json'],
        displayTypes: 'JSON',
        description: '將 Langflow Agent 版本 JSON 檔案上傳至資料庫',
        buttonText: '上傳 Agent 版本'
    }
};

// DOM Elements
const elements = {
    dropZone: document.querySelector('.drop-zone'),
    fileInput: document.getElementById('fileInput'),
    fileList: document.getElementById('fileList'),
    uploadBtn: document.getElementById('uploadBtn'),
    indexNameInput: document.getElementById('indexName'),
    indexTypeSelect: document.getElementById('indexType'),
    deleteExistingSelect: document.getElementById('deleteExisting'),
    progressBar: document.getElementById('progressBar'),
    progressFill: document.getElementById('progressFill'),
    statusMessage: document.getElementById('statusMessage'),
    headerDescription: document.getElementById('headerDescription'),
    supportedFormats: document.getElementById('supportedFormats')
};

// Event Listeners
document.addEventListener('DOMContentLoaded', function() {
    if (!elements.dropZone) return; // Exit if not on the right page

    // Initialize with default configuration
    updateUIForIndexType();

    // Index type change handler
    elements.indexTypeSelect.addEventListener('change', handleIndexTypeChange);

    // Drag and drop events
    elements.dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        elements.dropZone.classList.add('dragover');
    });

    elements.dropZone.addEventListener('dragleave', () => {
        elements.dropZone.classList.remove('dragover');
    });

    elements.dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        elements.dropZone.classList.remove('dragover');
        handleFiles(e.dataTransfer.files);
    });

    // Click to select
    elements.dropZone.addEventListener('click', () => elements.fileInput.click());
    elements.fileInput.addEventListener('change', (e) => handleFiles(e.target.files));

    // Upload button
    elements.uploadBtn.addEventListener('click', uploadFiles);
});

function handleIndexTypeChange() {
    // Clear existing files when switching index types
    selectedFiles = [];
    updateFileList();
    updateUploadButton();

    // Update UI elements
    updateUIForIndexType();

    showStatus(`已切換至 ${getCurrentConfig().indexName} 模式`, 'info');
}

function updateUIForIndexType() {
    const config = getCurrentConfig();

    // Update hidden index name input
    elements.indexNameInput.value = config.indexName;

    // Update file input accept attribute
    elements.fileInput.accept = config.allowedTypes.join(',');

    // Update UI text
    elements.headerDescription.textContent = config.description;
    elements.supportedFormats.textContent = `支援格式: ${config.displayTypes}`;
    elements.uploadBtn.textContent = config.buttonText;

    // For agent type, automatically set deleteExisting to true and disable the dropdown
    if (elements.indexTypeSelect.value === 'agent') {
        elements.deleteExistingSelect.value = 'true';
        elements.deleteExistingSelect.disabled = true;
        elements.deleteExistingSelect.style.opacity = '0.6';
        elements.deleteExistingSelect.style.cursor = 'not-allowed';

        // Add a note to explain why it's disabled
        const formGroup = elements.deleteExistingSelect.parentElement;
        let note = formGroup.querySelector('.agent-note');
        if (!note) {
            note = document.createElement('small');
            note.className = 'agent-note';
            note.style.color = '#666';
            note.style.fontStyle = 'italic';
            note.style.marginTop = '5px';
            note.textContent = '📝 Agent 版本會自動清除舊版本';
            formGroup.appendChild(note);
        }
    } else {
        // For documents type, enable the dropdown
        elements.deleteExistingSelect.disabled = false;
        elements.deleteExistingSelect.style.opacity = '1';
        elements.deleteExistingSelect.style.cursor = 'pointer';

        // Remove the note if it exists
        const formGroup = elements.deleteExistingSelect.parentElement;
        const note = formGroup.querySelector('.agent-note');
        if (note) {
            note.remove();
        }
    }
}

function getCurrentConfig() {
    return INDEX_CONFIGS[elements.indexTypeSelect.value];
}

function handleFiles(files) {
    const config = getCurrentConfig();
    const allowedTypes = config.allowedTypes;

    Array.from(files).forEach(file => {
        const extension = '.' + file.name.split('.').pop().toLowerCase();

        if (allowedTypes.includes(extension)) {
            if (!selectedFiles.some(f => f.name === file.name && f.size === file.size)) {
                selectedFiles.push(file);
            }
        } else {
            const currentMode = elements.indexTypeSelect.value === 'documents' ? '一般文件' : 'Agent 版本';
            showStatus(`檔案 "${file.name}" 不適用於 ${currentMode} 模式。請選擇 ${config.displayTypes} 格式的檔案。`, 'error');
        }
    });

    updateFileList();
    updateUploadButton();
}

function updateFileList() {
    elements.fileList.innerHTML = '';

    selectedFiles.forEach((file, index) => {
        const extension = file.name.split('.').pop().toLowerCase();
        const fileItem = document.createElement('div');
        fileItem.className = 'file-item';

        fileItem.innerHTML = `
            <div class="file-info">
                <div class="file-icon ${extension}">${extension.toUpperCase()}</div>
                <div>
                    <div>${file.name}</div>
                    <small>${formatFileSize(file.size)}</small>
                </div>
            </div>
            <button class="remove-btn" onclick="removeFile(${index})">移除</button>
        `;

        elements.fileList.appendChild(fileItem);
    });
}

function removeFile(index) {
    selectedFiles.splice(index, 1);
    updateFileList();
    updateUploadButton();
}

function updateUploadButton() {
    elements.uploadBtn.disabled = selectedFiles.length === 0;
}

function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function showStatus(message, type) {
    elements.statusMessage.textContent = message;
    elements.statusMessage.className = `status-message status-${type}`;
    elements.statusMessage.style.display = 'block';

    setTimeout(() => {
        elements.statusMessage.style.display = 'none';
    }, 5000);
}

function showProgress(show) {
    elements.progressBar.style.display = show ? 'block' : 'none';
}

function updateProgress(percent) {
    elements.progressFill.style.width = percent + '%';
}

async function uploadFiles() {
    if (selectedFiles.length === 0) return;

    const config = getCurrentConfig();
    const indexName = config.indexName;

    // For agent type, always delete existing regardless of user selection
    const deleteExisting = elements.indexTypeSelect.value === 'agent' ? 'true' : elements.deleteExistingSelect.value;

    elements.uploadBtn.disabled = true;
    elements.uploadBtn.textContent = '處理中...';
    showProgress(true);
    updateProgress(0);

    try {
        const formData = new FormData();
        selectedFiles.forEach(file => {
            formData.append('files', file);
        });
        formData.append('index_name', indexName);
        formData.append('deleteExisting', deleteExisting);
        formData.append('indexType', elements.indexTypeSelect.value); // Send index type for validation

        const response = await fetch(`${API_BASE}/upload`, {
            method: 'POST',
            body: formData
        });

        const result = await response.json();

        if (!response.ok || !result.success) {
            throw new Error(result.error || result.message || '上傳失敗');
        }

        updateProgress(100);
        const indexTypeText = elements.indexTypeSelect.value === 'documents' ? '一般文件' : 'Agent 版本';
        showStatus(`✅ 成功處理 ${selectedFiles.length} 個${indexTypeText}檔案至索引 "${indexName}"`, 'success');

        selectedFiles = [];
        updateFileList();

    } catch (error) {
        showStatus(`❌ 上傳失敗: ${error.message}`, 'error');
        console.error('Upload error:', error);
    } finally {
        elements.uploadBtn.disabled = false;
        elements.uploadBtn.textContent = config.buttonText;
        showProgress(false);
        updateUploadButton();
    }
}