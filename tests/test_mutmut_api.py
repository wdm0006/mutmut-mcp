import os
import pytest
from mutmut_mcp import clean_mutmut_cache, show_results

def test_clean_mutmut_cache_and_show_results():
    # Ensure .mutmut-cache does not exist
    cache_path = ".mutmut-cache"
    if os.path.exists(cache_path):
        os.remove(cache_path)
        
    # Test cleaning cache when none exists
    result = clean_mutmut_cache()
    assert "cache" in result.lower() or "no mutmut cache" in result.lower()
    # Test show_results returns an error or a string (should not crash)
    output = show_results()
    assert isinstance(output, str)
    # Should mention error or no results if mutmut never run
    assert "error" in output.lower() or "no mutmut cache" in output.lower() or output.strip() == "" 