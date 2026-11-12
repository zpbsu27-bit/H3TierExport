// Compiled offline by the Windows SDK fxc compiler and embedded in the EXE.
StructuredBuffer<float4> Source : register(t0);
StructuredBuffer<uint4> Ranges : register(t1);
StructuredBuffer<float> Weights : register(t2);
RWStructuredBuffer<float4> Destination : register(u0);
cbuffer Parameters : register(b0) { uint srcW; uint dstW; uint dstH; uint horizontal; uint rowBase; uint rowCount; uint pad0; uint pad1; };
[numthreads(8,8,1)]
void main(uint3 tid : SV_DispatchThreadID) {
 uint x=tid.x,y=tid.y+rowBase;
 if(x>=dstW || y>=dstH || tid.y>=rowCount)return;
 uint4 s=Ranges[horizontal!=0?x:y];
 uint pos=horizontal!=0?y*srcW+s.x:s.x*srcW+x;
 uint step=horizontal!=0?1:srcW;
 precise float4 sum=float4(0,0,0,0);
 [loop]for(uint k=0;k<s.y;k++){precise float4 term=Source[pos]*Weights[s.z+k];sum=sum+term;pos+=step;}
 Destination[y*dstW+x]=sum;
}
