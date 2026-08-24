cmake_minimum_required(VERSION 3.25)
project(pyalsoft_decoder LANGUAGES C)

add_library(pyalsoft_decoder SHARED pyalsoft_decoder.c)
target_compile_features(pyalsoft_decoder PRIVATE c_std_99)
target_compile_definitions(pyalsoft_decoder PRIVATE _CRT_SECURE_NO_WARNINGS)

if(MSVC)
    target_compile_options(pyalsoft_decoder PRIVATE /W4)
else()
    target_compile_options(pyalsoft_decoder PRIVATE -Wall -Wextra -Wpedantic)
    if(NOT WIN32)
        target_link_libraries(pyalsoft_decoder PRIVATE m)
    endif()
    set_target_properties(
        pyalsoft_decoder
        PROPERTIES C_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN YES
    )
endif()
