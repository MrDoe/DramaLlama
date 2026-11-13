# Native spatial renderer for Drama Llama.
#
# Wraps the ISC-licensed SAF "ambi_roomsim" example (shoebox room + image
# source method) and the SAF HOA/SH helpers into one shared library that the
# Python mixer loads through ctypes.
#
#   cmake -S src/drama_studio/spatial -B build/spatial
#   cmake --build build/spatial
#
# SAF lives outside this repo; point SAF_DIR at a checkout of the
# Spatial_Audio_Framework (default: the sibling SPARTA SDK tree).

cmake_minimum_required(VERSION 3.15)

project(drama_llama_spatial VERSION 1.0.0 LANGUAGES C)

if(NOT CMAKE_BUILD_TYPE OR CMAKE_BUILD_TYPE STREQUAL "")
    set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# ---------------------------------------------------------------- SAF location
set(SAF_DIR "" CACHE PATH "Path to a Spatial_Audio_Framework checkout")
if(SAF_DIR STREQUAL "")
    foreach(_candidate
            "${CMAKE_CURRENT_SOURCE_DIR}/../../../../SPARTA/SDKs/Spatial_Audio_Framework"
            "${CMAKE_CURRENT_SOURCE_DIR}/../../third_party/Spatial_Audio_Framework"
            "$ENV{DRAMA_LLAMA_SAF_DIR}")
        if(EXISTS "${_candidate}/framework/include/saf.h")
            set(SAF_DIR "${_candidate}")
            break()
        endif()
    endforeach()
endif()

if(NOT EXISTS "${SAF_DIR}/framework/include/saf.h")
    message(FATAL_ERROR
        "Spatial_Audio_Framework not found. Pass -DSAF_DIR=/path/to/Spatial_Audio_Framework")
endif()
get_filename_component(SAF_DIR "${SAF_DIR}" ABSOLUTE)
message(STATUS "SAF: ${SAF_DIR}")

# ------------------------------------------------------- SAF framework + roomsim
# Only the pieces we link are configured: no tests, no other examples, no SOFA.
set(SAF_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(SAF_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(SAF_BUILD_EXTRAS OFF CACHE BOOL "" FORCE)
set(SAF_ENABLE_SOFA_READER_MODULE OFF CACHE BOOL "" FORCE)
set(SAF_ENABLE_HADES_MODULE OFF CACHE BOOL "" FORCE)
set(SAF_ENABLE_TRACKER_MODULE OFF CACHE BOOL "" FORCE)

# CBLAS/LAPACKE: the only backend that needs nothing beyond a system BLAS.
# (MKL/IPP are optional; OpenBLAS ships with most distros.)
if(NOT DEFINED SAF_PERFORMANCE_LIB)
    if(APPLE)
        set(SAF_PERFORMANCE_LIB "SAF_USE_APPLE_ACCELERATE_LP64")
    else()
        set(SAF_PERFORMANCE_LIB "SAF_USE_OPEN_BLAS_AND_LAPACKE")
    endif()
endif()
set(SAF_PERFORMANCE_LIB "${SAF_PERFORMANCE_LIB}" CACHE STRING "" FORCE)

# The examples CMakeLists honours a pre-set `saf_example_list`; limiting it to
# ambi_roomsim keeps configure time (and the build) to the one module we need.
# `example_prefix` normally comes from SAF's top-level CMakeLists, which is
# bypassed here by adding the subdirectories directly.
set(example_prefix "saf_example_")
set(saf_example_list ambi_roomsim)

add_subdirectory("${SAF_DIR}/framework" "${CMAKE_BINARY_DIR}/saf_framework" EXCLUDE_FROM_ALL)
add_subdirectory("${SAF_DIR}/examples"  "${CMAKE_BINARY_DIR}/saf_examples"  EXCLUDE_FROM_ALL)

# ------------------------------------------------------------------ the shim
add_library(drama_llama_spatial SHARED spatial_shim.c)

target_include_directories(drama_llama_spatial PRIVATE
    "${SAF_DIR}/framework/include"
    "${SAF_DIR}/examples/include")

target_link_libraries(drama_llama_spatial
    PRIVATE
        saf
        saf_example_ambi_roomsim
        m)

# Hidden symbols keep the ctypes surface to the dl_* entry points.
set_target_properties(drama_llama_spatial PROPERTIES
    C_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN ON)

if(WIN32)
    target_link_libraries(drama_llama_spatial PRIVATE msvcrt)
endif()