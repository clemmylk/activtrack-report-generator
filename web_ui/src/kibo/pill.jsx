// Adapted from shadcnblocks/kibo packages/pill, MIT; original retained alongside.
import React from 'react';
export function Pill({children,className=''}){return <span className={'pill '+className}>{children}</span>}
export function PillIndicator({pulse=false}){return <span className={'indicator '+(pulse?'pulse':'')}/>}
