/** WebSocket client for real-time updates.
 *
 * Singleton socket with reference-counted lifecycle. Consumers call
 * `acquire()` on mount and `release()` on unmount; the socket only closes
 * when the last subscriber releases. Subscribers register handlers via
 * `on()`, which returns an unsubscribe function.
 *
 * Handlers survive transient reconnects (onclose -> attemptReconnect ->
 * connect) so subscribers don't need to re-register.
 *
 * Also listens for `visibilitychange` (tab becomes visible) and `online`
 * (network comes back) to reset the reconnect backoff and force a
 * reconnect when the tab has been asleep or the network was down.
 */
import { buildWebSocketUrl } from '../config/env';
import { logger } from '../utils/logger';

export type WebSocketMessageType =
  | 'sensor_update'
  | 'device_update'
  | 'mode_update'
  | 'initial_state'
  | 'pong'
  | 'schedule_update'
  | 'setpoint_update'
  | 'room_schedule_update'
  | 'climate_schedule_update';

export interface WebSocketMessage {
  type: WebSocketMessageType;
  [key: string]: unknown;
}

type MessageHandler = (message: WebSocketMessage) => void;

class WebSocketClient {
  private ws: WebSocket | null = null;
  private url: string;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 10;
  private reconnectDelay = 1000;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private handlers: Map<WebSocketMessageType, MessageHandler[]> = new Map();
  private isConnecting = false;
  private refCount = 0;

  constructor(url?: string) {
    this.url = url || buildWebSocketUrl();
    if (typeof window !== 'undefined') {
      window.addEventListener('visibilitychange', this.handleWake);
      window.addEventListener('online', this.handleWake);
    }
  }

  /** Increment subscriber count; open the socket if this is the first subscriber. */
  acquire(): void {
    this.refCount += 1;
    if (this.refCount === 1) {
      this.connect();
    }
  }

  /** Decrement subscriber count; close the socket when the last subscriber releases. */
  release(): void {
    if (this.refCount === 0) {
      logger.warn('WebSocket release() called with refCount=0 (double-release?)');
      return;
    }
    this.refCount -= 1;
    if (this.refCount === 0) {
      this.teardown();
    }
  }

  on(type: WebSocketMessageType, handler: MessageHandler): () => void {
    if (!this.handlers.has(type)) {
      this.handlers.set(type, []);
    }
    this.handlers.get(type)!.push(handler);

    return () => {
      const handlers = this.handlers.get(type);
      if (handlers) {
        const index = handlers.indexOf(handler);
        if (index > -1) {
          handlers.splice(index, 1);
        }
      }
    };
  }

  send(message: unknown): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    } else {
      logger.warn('WebSocket is not open, cannot send message');
    }
  }

  get readyState(): number {
    return this.ws?.readyState ?? WebSocket.CLOSED;
  }

  private connect(): void {
    if (this.ws?.readyState === WebSocket.OPEN || this.isConnecting) {
      return;
    }

    this.isConnecting = true;
    try {
      this.ws = new WebSocket(this.url);

      this.ws.onopen = () => {
        logger.info('WebSocket connected');
        this.isConnecting = false;
        this.reconnectAttempts = 0;
        this.reconnectDelay = 1000;
      };

      this.ws.onmessage = (event) => {
        try {
          const message: WebSocketMessage = JSON.parse(event.data);
          this.handleMessage(message);
        } catch (e) {
          logger.error('Error parsing WebSocket message:', e);
          logger.error('Raw message data:', event.data);
        }
      };

      this.ws.onerror = (error) => {
        logger.error('WebSocket error:', error);
        this.isConnecting = false;
      };

      this.ws.onclose = () => {
        logger.info('WebSocket disconnected');
        this.isConnecting = false;
        this.ws = null;
        this.attemptReconnect();
      };
    } catch (error) {
      logger.error('Error creating WebSocket:', error);
      this.isConnecting = false;
      this.attemptReconnect();
    }
  }

  private attemptReconnect(): void {
    if (this.refCount === 0) {
      return;
    }
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      logger.error('Max reconnection attempts reached');
      return;
    }

    this.reconnectAttempts++;
    const delay = Math.min(
      this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1),
      30000,
    );
    logger.debug(
      `Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts})`,
    );

    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
    }
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  private handleMessage(message: WebSocketMessage): void {
    const handlers = this.handlers.get(message.type) || [];
    handlers.forEach((handler) => {
      try {
        handler(message);
      } catch (error) {
        logger.error('Error in WebSocket message handler:', error);
      }
    });
  }

  private teardown(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.onopen = null;
      this.ws.onmessage = null;
      this.ws.onerror = null;
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
    this.reconnectAttempts = 0;
    this.reconnectDelay = 1000;
    this.isConnecting = false;
  }

  private handleWake = (): void => {
    if (this.refCount === 0) {
      return;
    }
    if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
      return;
    }
    if (this.ws?.readyState === WebSocket.OPEN || this.isConnecting) {
      return;
    }
    logger.debug('Visibility/online wake event; resetting reconnect backoff');
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.reconnectAttempts = 0;
    this.reconnectDelay = 1000;
    this.connect();
  };
}

export const wsClient = new WebSocketClient();
