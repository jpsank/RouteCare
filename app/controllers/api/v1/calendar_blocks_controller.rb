class Api::V1::CalendarBlocksController < Api::V1::BaseController
  def index
    blocks = current_user.calendar_blocks.order(:starts_at)
    render json: { calendar_blocks: blocks.map { |block| serialize_block(block) } }
  end

  def create
    block = current_user.calendar_blocks.new(calendar_block_params)
    if block.save
      render json: { calendar_block: serialize_block(block) }, status: :created
      return
    end

    render_unprocessable(block.errors.full_messages)
  end

  def update
    block = current_user.calendar_blocks.find(params[:id])
    block.update!(calendar_block_params)
    render json: { calendar_block: serialize_block(block) }
  rescue ActiveRecord::RecordInvalid
    render_unprocessable(block.errors.full_messages)
  end

  def destroy
    block = current_user.calendar_blocks.find(params[:id])
    block.destroy!
    head :no_content
  end

  private

  def calendar_block_params
    params.require(:calendar_block).permit(:source, :external_event_id, :title, :starts_at, :ends_at, metadata: {})
  end

  def serialize_block(block)
    {
      id: block.id,
      source: block.source,
      title: block.title,
      starts_at: block.starts_at,
      ends_at: block.ends_at,
      metadata: block.metadata
    }
  end
end
