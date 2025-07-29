class ChatBot {
    constructor() {
        this.chatMessages = document.getElementById('chatMessages');
        this.chatForm = document.getElementById('chatForm');
        this.chatInput = document.getElementById('chatInput');
        this.sendButton = document.getElementById('sendButton');
        this.typingIndicator = document.getElementById('typingIndicator');

//        this.apiUrl = 'http://localhost:8000/api/chat';
        this.apiUrl = '/api/chat'; // Use relative path for API
        this.sessionId = this.generateSessionId();

        this.initializeMarkdown();
        this.initializeEventListeners();
    }

    initializeMarkdown() {
        // Configure marked.js options
        if (typeof marked !== 'undefined') {
            marked.setOptions({
                breaks: true, // Convert line breaks to <br>
                gfm: true, // GitHub Flavored Markdown
                headerIds: false, // Don't add IDs to headers
                mangle: false, // Don't mangle autolinks
                sanitize: false, // Allow HTML (be careful with user input)
            });
        } else {
            console.error("marked.js library is not loaded.");
        }
    }

    initializeEventListeners() {
        this.chatForm.addEventListener('submit', (e) => this.handleSubmit(e));
        this.chatInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.handleSubmit(e);
            }
        });
    }

    async handleSubmit(e) {
        e.preventDefault();

        const message = this.chatInput.value.trim();
        if (!message) return;

        // Add user message to chat
        this.addMessage(message, 'user');
        this.chatInput.value = '';
        this.setLoading(true);

        try {
            // Send message to backend
            const response = await fetch(this.apiUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    message: message,
                    session_id: this.sessionId
                })
            });

            const data = await response.json();

            if (!response.ok || !data.success) {
                 const errorMsg = data.error || data.message || `HTTP error! status: ${response.status}`;
                 throw new Error(errorMsg);
            }

            // Add bot response to chat
            this.addMessage(data.data.response, 'bot');

        } catch (error) {
            console.error('Error:', error);
            this.addMessage(`抱歉，我遇到了一個錯誤: ${error.message}。請再試一次。`, 'bot', true);
        } finally {
            this.setLoading(false);
        }
    }

    addMessage(text, sender, isError = false) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${sender}`;

        const contentDiv = document.createElement('div');
        contentDiv.className = `message-content ${isError ? 'error-message' : ''}`;

        if (sender === 'bot' && !isError && typeof marked !== 'undefined') {
            // Parse markdown for bot messages
            try {
                contentDiv.innerHTML = marked.parse(text);
            } catch (error) {
                console.error('Markdown parsing error:', error);
                contentDiv.textContent = text; // Fallback to plain text
            }
        } else {
            // Plain text for user messages and errors
            contentDiv.textContent = text;
        }

        messageDiv.appendChild(contentDiv);
        this.chatMessages.appendChild(messageDiv);

        // Scroll to bottom
        this.scrollToBottom();
    }

    setLoading(isLoading) {
        this.sendButton.disabled = isLoading;
        this.chatInput.disabled = isLoading;

        if (isLoading) {
            this.sendButton.textContent = '傳送中...';
            this.typingIndicator.style.display = 'flex';
        } else {
            this.sendButton.textContent = '傳送';
            this.typingIndicator.style.display = 'none';
        }

        this.scrollToBottom();
    }

    scrollToBottom() {
        setTimeout(() => {
            this.chatMessages.scrollTop = this.chatMessages.scrollHeight;
        }, 100);
    }

    generateSessionId() {
        // Generate a simple session ID
        return 'session_' + Math.random().toString(36).substr(2, 9) + '_' + Date.now();
    }
}

// Initialize chatbot when page loads
document.addEventListener('DOMContentLoaded', () => {
    new ChatBot();
});