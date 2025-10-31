"""Colored logging utility for the bot."""
import logging
import sys
from typing import Optional

class ColoredFormatter(logging.Formatter):
    """Custom formatter that adds colors to log levels."""
    # ANSI color codes
    BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE = range(8)
    RESET_SEQ = "\033[0m"
    COLOR_SEQ = "\033[1;%dm"
    BOLD_SEQ = "\033[1m"

    COLORS = {
        'WARNING': YELLOW,
        'INFO': WHITE,
        'DEBUG': BLUE,
        'CRITICAL': YELLOW,
        'ERROR': RED
    }

    def format(self, record):
        levelname = record.levelname
        if levelname in self.COLORS:
            levelname_color = self.COLOR_SEQ % (30 + self.COLORS[levelname]) + levelname + self.RESET_SEQ
            record.levelname = levelname_color
            


            
        return logging.Formatter.format(self, record)

def setup_logger(name: str, log_level: int = logging.DEBUG, log_file: Optional[str] = None) -> logging.Logger:
    """Set up a logger with colored output.
    
    Args:
        name: Logger name
        log_level: Logging level (default: INFO)
        log_file: Optional file to log to (default: None, logs to console only)
    
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(log_level)
    
    # Prevent adding multiple handlers if logger already configured
    if logger.handlers:
        return logger

    # Create formatter
    formatter = ColoredFormatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # File handler if specified
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

    return logger

def setup_ai_logger() -> logging.Logger:
    """Set up a dedicated logger for AI requests."""
    logger = logging.getLogger('ai_requests')
    logger.setLevel(logging.INFO)
    
    # Prevent adding multiple handlers if logger already configured
    if logger.handlers:
        return logger

    # File handler
    file_handler = logging.FileHandler('ai_requests.log', encoding='utf-8')
    file_formatter = logging.Formatter(
        '%(asctime)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    return logger

# Create root logger
logger = setup_logger('liforrabot')
