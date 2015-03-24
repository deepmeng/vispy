// ----------------------------------------------------------------------------
// Copyright (c) 2014, Nicolas P. Rougier. All Rights Reserved.
// Distributed under the (new) BSD License.
// ----------------------------------------------------------------------------

// Varyings
// ------------------------------------
varying float v_size;
varying vec4  v_color;

void main(void)
{
    <viewport.clipping>;

    gl_FragColor = v_color;
}
